"""SqliteLearningStore - the learning layer's persistence (PLAN §7, T24).

One SQLite file under `DATA_DIR` (`learning.db`, WAL). It outlives job purges and restarts, which
is safe because it holds no value: every write passes `assert_pii_free` (a leak raises, it is not
logged and skipped), the observation model has no value field, and the digits of a shape are
discarded before they get here. A hint is used only once confirmed `LEARNING_PROMOTION_HITS` times
(one correction is an anecdote, three is a pattern); a label synonym or a proposed institution also
needs an explicit approval, so one mistaken confirmation cannot teach something wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Final, Literal

import structlog

from backend.core.learning import assert_pii_free, shape_to_regex
from backend.core.models import (
    FieldKey,
    LayoutHint,
    LayoutObservation,
    PatternHint,
    RegionKind,
    RunMetric,
)
from backend.settings import Settings

log = structlog.get_logger()

SCHEMA_PATH: Final = Path(__file__).with_name("schema.sql")
DB_FILENAME: Final = "learning.db"
# Structural: seconds a writer waits for another connection's lock before giving up.
_BUSY_TIMEOUT: Final = 10.0
# Structural: a hint's confidence grows with its hits and saturates here; S4 caps it again by
# `learned_region_score`, so a learned region always competes instead of overriding.
_CONFIDENCE_SATURATION_HITS: Final = 10

ProposalKind = Literal["label", "sender"]


class SqliteLearningStore:
    """LearningStorePort over SQLite. Connections are short-lived, one per operation, so the store
    is safe to share between the event loop and worker threads."""

    def __init__(self, *, settings: Settings, path: Path | None = None) -> None:
        self._path = path or Path(settings.data_dir) / DB_FILENAME
        self._promotion = settings.learning_promotion_hits
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT)
        db.row_factory = sqlite3.Row
        try:
            with db:  # commit on success, roll back on error
                yield db
        finally:
            db.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(tz=UTC).isoformat(timespec="seconds")

    # ------------------------------------------------------------------ writes

    def record_confirmation(self, obs: LayoutObservation) -> None:
        """Store one confirmed observation: upsert the layout hint (the box becomes the running
        mean of the boxes seen), the shape and the label text, each counting a hit.

        Raises PiiLeak (a ValueError) if `obs` carries a forbidden key, before anything is written."""
        assert_pii_free(obs)
        y0, x0, y1, x1 = obs.box
        region = (obs.region_kind or RegionKind.patient_box).value
        label = obs.label_text or ""
        now = self._now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO layout_hint (sender, field, page_role, region_kind, y_min, x_min,
                    y_max, x_max, label_text, source_kind, hits, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT (sender, field, page_role, label_text, source_kind) DO UPDATE SET
                    y_min = (y_min * hits + excluded.y_min) / (hits + 1),
                    x_min = (x_min * hits + excluded.x_min) / (hits + 1),
                    y_max = (y_max * hits + excluded.y_max) / (hits + 1),
                    x_max = (x_max * hits + excluded.x_max) / (hits + 1),
                    region_kind = excluded.region_kind,
                    hits = hits + 1, last_seen = excluded.last_seen
                """,
                (
                    obs.sender,
                    obs.field.value,
                    obs.page_role,
                    region,
                    y0,
                    x0,
                    y1,
                    x1,
                    label,
                    obs.source.value,
                    now,
                ),
            )
            if obs.shape:
                db.execute(
                    """
                    INSERT INTO pattern_hint (sender, field, shape, hits, last_seen)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT (sender, field, shape) DO UPDATE SET
                        hits = hits + 1, last_seen = excluded.last_seen
                    """,
                    (obs.sender, obs.field.value, obs.shape, now),
                )
            if label:
                db.execute(
                    """
                    INSERT INTO label_synonym (sender, field, label_text) VALUES (?, ?, ?)
                    ON CONFLICT (sender, field, label_text) DO UPDATE SET hits = hits + 1
                    """,
                    (obs.sender, obs.field.value, label),
                )

    def propose_sender(self, canonical: str, keywords: list[str]) -> None:
        """Record that a person confirmed an institution that is not in the closed list. It is a
        proposal: it changes nothing until `approve("sender", id)`."""
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO sender_proposal (canonical, keywords) VALUES (?, ?)
                ON CONFLICT (canonical) DO UPDATE SET hits = hits + 1
                """,
                (canonical, json.dumps(keywords, ensure_ascii=False)),
            )

    def approve(self, kind: ProposalKind, proposal_id: int) -> bool:
        """Mark a label synonym or a sender proposal approved (an explicit human act). Returns
        False when there is no such proposal."""
        table = "label_synonym" if kind == "label" else "sender_proposal"
        with self._connect() as db:
            cursor = db.execute(f"UPDATE {table} SET approved = 1 WHERE id = ?", (proposal_id,))  # noqa: S608
            return cursor.rowcount > 0

    def record_run(self, metric: RunMetric) -> None:
        """Append one finished job's counters to the learning curve."""
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO run_metric (at, fields_total, fields_closed_local, fields_escalated,
                    fields_flagged, local_close_rate, provider_calls, engine_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    metric.at.isoformat(timespec="seconds"),
                    metric.fields_total,
                    metric.fields_closed_local,
                    metric.fields_escalated,
                    metric.fields_flagged,
                    metric.local_close_rate,
                    metric.provider_calls,
                    metric.engine_version,
                ),
            )

    # ------------------------------------------------------------------ hints (the port)

    def layout_hints(self, sender: str, field: str) -> list[LayoutHint]:
        """Promoted locations for (sender, field), most confirmed first. Empty when none has
        reached the promotion threshold."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT region_kind, y_min, x_min, y_max, x_max, hits FROM layout_hint
                WHERE sender = ? AND field = ? AND hits >= ? ORDER BY hits DESC, id
                """,
                (sender, field, self._promotion),
            ).fetchall()
        return [
            LayoutHint(
                region_kind=RegionKind(r["region_kind"]),
                box=(r["y_min"], r["x_min"], r["y_max"], r["x_max"]),
                confidence=min(1.0, r["hits"] / _CONFIDENCE_SATURATION_HITS),
            )
            for r in rows
        ]

    def pattern_hints(self, sender: str, field: str) -> list[PatternHint]:
        """Promoted shapes for (sender, field) as anchored regexes, most confirmed first."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT shape, hits FROM pattern_hint
                WHERE sender = ? AND field = ? AND hits >= ? ORDER BY hits DESC, id
                """,
                (sender, field, self._promotion),
            ).fetchall()
        return [
            PatternHint(
                pattern=shape_to_regex(r["shape"]),
                confidence=min(1.0, r["hits"] / _CONFIDENCE_SATURATION_HITS),
            )
            for r in rows
        ]

    def label_synonyms(self, sender: str, field: str) -> list[str]:
        """Label texts that are approved AND confirmed enough times. An unapproved one is never
        returned, however often it was seen."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT label_text FROM label_synonym
                WHERE sender = ? AND field = ? AND approved = 1 AND hits >= ? ORDER BY hits DESC, id
                """,
                (sender, field, self._promotion),
            ).fetchall()
        return [r["label_text"] for r in rows]

    def approved_senders(self) -> list[tuple[str, list[str]]]:
        """(canonical, keywords) of every approved, sufficiently confirmed sender proposal."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT canonical, keywords FROM sender_proposal WHERE approved = 1 AND hits >= ?",
                (self._promotion,),
            ).fetchall()
        return [(r["canonical"], list(json.loads(r["keywords"]))) for r in rows]

    # ------------------------------------------------------------------ reads for the API

    def run_metrics(self, limit: int = 200) -> list[dict[str, Any]]:
        """The learning curve, oldest first: the last `limit` runs."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT * FROM run_metric ORDER BY id DESC LIMIT ?) ORDER BY id",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def proposals(self) -> dict[str, list[dict[str, Any]]]:
        """Label synonyms and sender proposals waiting for (or holding) approval."""
        with self._connect() as db:
            labels = [dict(r) for r in db.execute("SELECT * FROM label_synonym ORDER BY id")]
            senders = [dict(r) for r in db.execute("SELECT * FROM sender_proposal ORDER BY id")]
        for entry in senders:
            entry["keywords"] = json.loads(entry["keywords"])
        return {"labels": labels, "senders": senders}

    def seed(self) -> dict[str, list[dict[str, Any]]]:
        """Every promoted layout and shape as a PII-free document (`learning_seed.json`): boxes,
        shapes and counters, which a developer can commit (PLAN §7.4)."""
        with self._connect() as db:
            layouts = [
                dict(r)
                for r in db.execute(
                    """
                    SELECT sender, field, page_role, region_kind, y_min, x_min, y_max, x_max,
                           label_text, source_kind, hits FROM layout_hint WHERE hits >= ?
                    ORDER BY sender, field, id
                    """,
                    (self._promotion,),
                )
            ]
            shapes = [
                dict(r)
                for r in db.execute(
                    "SELECT sender, field, shape, hits FROM pattern_hint WHERE hits >= ? "
                    "ORDER BY sender, field, id",
                    (self._promotion,),
                )
            ]
        return {"layout_hints": layouts, "pattern_hints": shapes}

    def import_seed(self, seed: dict[str, Any]) -> int:
        """Load a seed into an EMPTY store (so a restart never double-counts). Returns the number
        of rows loaded; every entry is checked to carry only the seed's own keys."""
        allowed_layout = {
            "sender", "field", "page_role", "region_kind", "y_min", "x_min", "y_max", "x_max",
            "label_text", "source_kind", "hits",
        }  # fmt: skip
        allowed_shape = {"sender", "field", "shape", "hits"}
        with self._connect() as db:
            if db.execute("SELECT 1 FROM layout_hint LIMIT 1").fetchone() is not None:
                return 0
            now = self._now()
            loaded = 0
            for entry in seed.get("layout_hints", []):
                if set(entry) - allowed_layout:
                    raise ValueError("seed layout entry has unexpected keys")
                FieldKey(entry["field"])
                RegionKind(entry["region_kind"])
                db.execute(
                    """
                    INSERT OR IGNORE INTO layout_hint (sender, field, page_role, region_kind,
                        y_min, x_min, y_max, x_max, label_text, source_kind, hits, last_seen)
                    VALUES (:sender, :field, :page_role, :region_kind, :y_min, :x_min, :y_max,
                        :x_max, :label_text, :source_kind, :hits, :now)
                    """,
                    {**entry, "now": now},
                )
                loaded += 1
            for entry in seed.get("pattern_hints", []):
                if set(entry) - allowed_shape:
                    raise ValueError("seed pattern entry has unexpected keys")
                db.execute(
                    "INSERT OR IGNORE INTO pattern_hint (sender, field, shape, hits, last_seen) "
                    "VALUES (:sender, :field, :shape, :hits, :now)",
                    {**entry, "now": now},
                )
                loaded += 1
        return loaded

    def dump_tables(self) -> dict[str, list[dict[str, Any]]]:
        """Every row of every table, for the privacy audit (a manual dump must show no name, ID,
        date or form value)."""
        out: dict[str, list[dict[str, Any]]] = {}
        with self._connect() as db:
            for table in (
                "layout_hint",
                "pattern_hint",
                "label_synonym",
                "sender_proposal",
                "run_metric",
            ):
                out[table] = [dict(r) for r in db.execute(f"SELECT * FROM {table}")]  # noqa: S608
        return out
