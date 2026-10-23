-- Learning store schema (PLAN §7.2). Locations, abstracted shapes, label texts, institution
-- names and counters only: no name, ID, date, central or commitment number, text or image.
-- Applied idempotently at start-up.
--
-- Differences from the plan's sketch, both so that a UNIQUE key works in SQLite (where NULLs are
-- always distinct): `label_text` is NOT NULL DEFAULT '' (empty = no label), and layout_hint
-- carries `region_kind`, which S4 needs to turn a stored location back into a Region.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS layout_hint (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL, field TEXT NOT NULL, page_role TEXT NOT NULL,
    region_kind TEXT NOT NULL,
    y_min REAL NOT NULL CHECK (y_min BETWEEN 0 AND 1),
    x_min REAL NOT NULL CHECK (x_min BETWEEN 0 AND 1),
    y_max REAL NOT NULL CHECK (y_max BETWEEN 0 AND 1),
    x_max REAL NOT NULL CHECK (x_max BETWEEN 0 AND 1),
    label_text TEXT NOT NULL DEFAULT '', source_kind TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1, last_seen TEXT NOT NULL,
    UNIQUE (sender, field, page_role, label_text, source_kind)
);
CREATE INDEX IF NOT EXISTS ix_layout_lookup ON layout_hint (sender, field);

CREATE TABLE IF NOT EXISTS pattern_hint (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL, field TEXT NOT NULL, shape TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1, last_seen TEXT NOT NULL,
    UNIQUE (sender, field, shape)
);

CREATE TABLE IF NOT EXISTS label_synonym (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL, field TEXT NOT NULL, label_text TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1, approved INTEGER NOT NULL DEFAULT 0,
    UNIQUE (sender, field, label_text)
);

CREATE TABLE IF NOT EXISTS field_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL, field TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0, corrected INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL,
    UNIQUE (sender, field)
);

-- Which reader of a disputed name held the value a person chose: counts only, never the name.
CREATE TABLE IF NOT EXISTS reader_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field TEXT NOT NULL, reader TEXT NOT NULL,
    disputes INTEGER NOT NULL DEFAULT 0, right_count INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT NOT NULL,
    UNIQUE (field, reader)
);

CREATE TABLE IF NOT EXISTS sender_proposal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical TEXT NOT NULL UNIQUE, keywords TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 1, approved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_metric (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
    fields_total INTEGER NOT NULL, fields_closed_local INTEGER NOT NULL,
    fields_escalated INTEGER NOT NULL, fields_flagged INTEGER NOT NULL,
    local_close_rate REAL NOT NULL, provider_calls INTEGER NOT NULL,
    engine_version TEXT NOT NULL
);
