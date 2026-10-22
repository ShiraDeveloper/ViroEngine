"""TrOcrDigits - OcrPort for handwritten digits with TrOCR-small in ONNX (PLAN §4 S7, T13).

The model is ``microsoft/trocr-small-handwritten`` exported to int8 ONNX by
``tools/export_trocr.py``: an image encoder and a token decoder, plus a vocabulary file. It is
a line reader, so only ``read_crop`` reads anything; a full page is not its job.

* **Lazy and cached.** Nothing is loaded until the first crop is read, and the sessions are
  kept for the life of the adapter, so a batch with no handwriting pays nothing.
* **Up to N candidates.** A beam search of width ``trocr_candidates`` returns distinct
  readings best first in ``OcrResult.candidates``; the best one is also ``text`` and the only
  word, with the beam's mean token probability as its confidence. This confidence is never a
  reason to accept a value (S8 decides).
* **Whitelist.** With a whitelist, only tokens made of whitelisted characters may be emitted,
  so a digits whitelist cannot produce a letter.
* **Graceful absence.** Missing, unreadable or over-budget model files make every read return
  an empty result (logged once); the field then escalates instead of the pipeline failing.

ASSUMPTION (PLAN T13): trained on Latin handwriting, so digits should transfer and Hebrew
letters should not. Measured in the T17 bake-off; dropped per the cut list if it loses.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import io
import json
from pathlib import Path
from typing import Any, Final, Protocol

import numpy as np
from numpy.typing import NDArray
from PIL import Image, UnidentifiedImageError
import structlog

from backend.core.models import ImageRef, OcrResult, OcrWord, ReaderName
from backend.core.ports.storage import StoragePort
from backend.settings import Settings

log = structlog.get_logger()

# SentencePiece marks a word boundary with this character in its token strings.
_SPACE_MARK: Final = "\u2581"
# The single "word" of a crop reading covers the whole crop.
_WHOLE_CROP: Final = (0.0, 0.0, 1.0, 1.0)
_BYTES_PER_MB: Final = 1e6

Floats = NDArray[np.float32]


class InferenceSession(Protocol):
    """The part of ``onnxruntime.InferenceSession`` this adapter uses."""

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]: ...


SessionFactory = Callable[[Path], InferenceSession]


def _onnx_session(path: Path) -> InferenceSession:
    """A CPU onnxruntime session for `path` (imported here, so tests need no runtime)."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3  # errors only
    session: InferenceSession = ort.InferenceSession(
        str(path), sess_options=options, providers=["CPUExecutionProvider"]
    )
    return session


@dataclass(frozen=True)
class _Vocab:
    """Token strings, special ids and the encoder's image preprocessing."""

    tokens: list[str]
    start_id: int
    eos_id: int
    special_ids: frozenset[int]
    height: int
    width: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    rescale: float

    @classmethod
    def load(cls, path: Path) -> _Vocab:
        """Parse the vocabulary file written by tools/export_trocr.py. Raises ValueError,
        KeyError or OSError on a malformed or missing file."""
        meta = json.loads(path.read_text(encoding="utf-8"))
        mean, std = meta["image_mean"], meta["image_std"]
        return cls(
            tokens=[str(t) for t in meta["tokens"]],
            start_id=int(meta["decoder_start_token_id"]),
            eos_id=int(meta["eos_token_id"]),
            special_ids=frozenset(int(i) for i in meta["special_ids"]),
            height=int(meta["image_height"]),
            width=int(meta["image_width"]),
            mean=(float(mean[0]), float(mean[1]), float(mean[2])),
            std=(float(std[0]), float(std[1]), float(std[2])),
            rescale=float(meta["rescale_factor"]),
        )


@dataclass(frozen=True)
class _Model:
    vocab: _Vocab
    encoder: InferenceSession
    decoder: InferenceSession


class TrOcrDigits:
    """Read handwritten digit crops with TrOCR-small (ONNX); never raises."""

    name = "trocr"

    def __init__(
        self,
        *,
        settings: Settings,
        storage: StoragePort,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._settings = settings
        self._storage = storage
        self._factory: SessionFactory = session_factory or _onnx_session
        self._model: _Model | None = None
        self._unavailable = False
        self._allowed: dict[str | None, NDArray[np.bool_]] = {}

    # ------------------------------------------------------------------ OcrPort

    def read_page(self, image: ImageRef, langs: str = "heb+eng") -> OcrResult:
        """Not a page reader: always an empty result."""
        return _empty()

    def read_crop(
        self,
        image: ImageRef,
        *,
        whitelist: str | None = None,
        psm: int = 7,
        langs: str | None = None,
    ) -> OcrResult:
        """Read one handwritten line. `psm` and `langs` do not apply to this reader and are
        ignored. Up to ``trocr_candidates`` distinct readings, best first. Never raises."""
        model = self._load()
        if model is None:
            return _empty()
        pixels = self._pixels(image, model.vocab)
        if pixels is None:
            return _empty()
        try:
            hidden = np.asarray(model.encoder.run(None, {"pixel_values": pixels})[0])
            readings = self._beam_search(model, hidden, whitelist)
        except (RuntimeError, ValueError, IndexError) as exc:  # onnxruntime raises these
            log.warning("trocr_inference_failed", error_type=type(exc).__name__)
            return _empty()
        if not readings:
            return _empty()
        best_text, best_conf = readings[0]
        return OcrResult(
            reader=ReaderName.trocr,
            words=[OcrWord(text=best_text, box=_WHOLE_CROP, confidence=best_conf)],
            text=best_text,
            candidates=[text for text, _ in readings],
        )

    def detect_orientation(self, image: ImageRef) -> int | None:
        """A line reader cannot tell a page's orientation: always abstains."""
        return None

    # ------------------------------------------------------------------ loading

    @property
    def loaded(self) -> bool:
        """True once the model has been loaded (for health reporting and tests)."""
        return self._model is not None

    def model_paths(self) -> tuple[Path, Path, Path]:
        """Encoder, decoder and vocabulary paths from Settings."""
        root = Path(self._settings.models_dir)
        s = self._settings
        return root / s.trocr_vision_file, root / s.trocr_text_file, root / s.trocr_vocab_file

    def model_size_mb(self) -> float:
        """Total size of the installed model files in MB; 0 for files that are absent."""
        return sum(p.stat().st_size for p in self.model_paths() if p.is_file()) / _BYTES_PER_MB

    def _load(self) -> _Model | None:
        """The model, loaded on first use and cached; None when it cannot be used."""
        if self._model is not None or self._unavailable:
            return self._model
        paths = self.model_paths()
        encoder_path, decoder_path, vocab_path = paths
        if not all(p.is_file() for p in paths):
            self._give_up("trocr_model_missing")
            return None
        if self.model_size_mb() > self._settings.trocr_max_model_mb:
            self._give_up("trocr_model_over_budget")
            return None
        try:
            vocab = _Vocab.load(vocab_path)
            self._model = _Model(
                vocab=vocab,
                encoder=self._factory(encoder_path),
                decoder=self._factory(decoder_path),
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._give_up("trocr_model_unreadable", error_type=type(exc).__name__)
            return None
        log.info("trocr_loaded", size_mb=round(self.model_size_mb(), 1))
        return self._model

    def _give_up(self, event: str, **context: str) -> None:
        """Mark the reader unavailable for the life of the adapter, logging the reason once."""
        self._unavailable = True
        log.warning(event, **context)

    # ------------------------------------------------------------------ inference

    def _pixels(self, image: ImageRef, vocab: _Vocab) -> Floats | None:
        """The crop as a normalized [1, 3, H, W] float tensor, or None when unreadable."""
        data = self._storage.get(image)
        if not data:
            return None
        try:
            with Image.open(io.BytesIO(data)) as img:
                rgb = img.convert("RGB").resize(
                    (vocab.width, vocab.height), Image.Resampling.BILINEAR
                )
        except (UnidentifiedImageError, OSError, ValueError):
            return None
        array = np.asarray(rgb, dtype=np.float32) * vocab.rescale
        mean = np.asarray(vocab.mean, dtype=np.float32)
        std = np.asarray(vocab.std, dtype=np.float32)
        array = (array - mean) / std
        return np.ascontiguousarray(array.transpose(2, 0, 1)[np.newaxis], dtype=np.float32)

    def _allowed_tokens(self, vocab: _Vocab, whitelist: str | None) -> NDArray[np.bool_]:
        """Mask of the tokens that may be emitted under `whitelist` (cached per whitelist).

        A token is allowed when, without its word-boundary mark, it is non-empty and every
        character is whitelisted; special tokens never are, except end-of-sequence.
        """
        cached = self._allowed.get(whitelist)
        if cached is not None:
            return cached
        chars = set(whitelist) if whitelist else None
        allowed = np.zeros(len(vocab.tokens), dtype=np.bool_)
        for index, token in enumerate(vocab.tokens):
            if index in vocab.special_ids:
                continue
            text = token.replace(_SPACE_MARK, "")
            if text and (chars is None or set(text) <= chars):
                allowed[index] = True
        allowed[vocab.eos_id] = True
        self._allowed[whitelist] = allowed
        return allowed

    def _beam_search(
        self, model: _Model, hidden: NDArray[Any], whitelist: str | None
    ) -> list[tuple[str, float]]:
        """Distinct readings with their mean token probability, best first.

        The decoder is re-run over each beam's whole prefix (no key/value cache): a digit
        field is a dozen tokens at most.
        """
        vocab = model.vocab
        width = self._settings.trocr_candidates
        allowed = self._allowed_tokens(vocab, whitelist)
        beams: list[tuple[list[int], float]] = [([vocab.start_id], 0.0)]
        finished: list[tuple[list[int], float]] = []
        for _ in range(self._settings.trocr_max_length):
            ids = np.asarray([b[0] for b in beams], dtype=np.int64)
            states = np.repeat(hidden, len(beams), axis=0)
            logits = np.asarray(
                model.decoder.run(None, {"input_ids": ids, "encoder_hidden_states": states})[0],
                dtype=np.float64,
            )[:, -1, :]
            logprobs = _log_softmax(logits)
            # The model's output layer may be wider than the tokenizer: extra ids are never
            # emitted.
            mask = np.zeros(logprobs.shape[1], dtype=np.bool_)
            shared = min(len(mask), len(allowed))
            mask[:shared] = allowed[:shared]
            logprobs[:, ~mask] = -np.inf
            expansions: list[tuple[list[int], float]] = []
            for (prefix, score), row in zip(beams, logprobs, strict=True):
                for token in np.argsort(row)[::-1][:width]:
                    step = float(row[token])
                    if not np.isfinite(step):
                        break
                    expansions.append(([*prefix, int(token)], score + step))
            expansions.sort(key=lambda e: e[1], reverse=True)
            beams = []
            for prefix, score in expansions:
                if prefix[-1] == vocab.eos_id:
                    if len(prefix) > 2:  # at least one real token before the end
                        finished.append((prefix, score))
                elif len(beams) < width:
                    beams.append((prefix, score))
            if not beams or len(finished) >= width:
                break
        finished.extend(beams)  # readings cut off at the token limit still count
        return _rank(finished, vocab, whitelist, width)


def _rank(
    hypotheses: Sequence[tuple[list[int], float]],
    vocab: _Vocab,
    whitelist: str | None,
    limit: int,
) -> list[tuple[str, float]]:
    """Distinct decoded texts ranked by mean token log-probability, at most `limit`."""
    scored: list[tuple[float, str]] = []
    for prefix, score in hypotheses:
        body = [t for t in prefix[1:] if t != vocab.eos_id]
        if not body:
            continue
        text = _decode(body, vocab, whitelist)
        if text:
            scored.append((score / len(body), text))
    scored.sort(key=lambda s: s[0], reverse=True)
    seen: set[str] = set()
    ranked: list[tuple[str, float]] = []
    for mean_logprob, text in scored:
        if text in seen:
            continue
        seen.add(text)
        ranked.append((text, float(np.exp(mean_logprob))))
        if len(ranked) >= limit:
            break
    return ranked


def _decode(ids: Sequence[int], vocab: _Vocab, whitelist: str | None) -> str:
    """Token ids to text. Under a whitelist the word-boundary spaces are dropped, since a
    whitelisted field is one token run (digits do not carry spaces)."""
    text = "".join(vocab.tokens[i] for i in ids if i not in vocab.special_ids)
    spaced = text.replace(_SPACE_MARK, " ")
    return spaced.replace(" ", "") if whitelist else " ".join(spaced.split())


def _log_softmax(logits: NDArray[np.float64]) -> NDArray[np.float64]:
    shifted = logits - logits.max(axis=1, keepdims=True)
    result: NDArray[np.float64] = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
    return result


def _empty() -> OcrResult:
    return OcrResult(reader=ReaderName.trocr, words=[], text="", candidates=[])
