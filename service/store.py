"""SQLite transaction boundaries and views over immutable source data."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name TEXT NOT NULL, created TEXT NOT NULL, scope_mode TEXT NOT NULL DEFAULT 'unknown',
    scope TEXT NOT NULL DEFAULT '[]', result TEXT, verified_at TEXT, stale INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, batch TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    role TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, supplier TEXT NOT NULL,
    coverage TEXT NOT NULL, digest TEXT NOT NULL, original BLOB NOT NULL,
    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', snapshot_id INTEGER,
    UNIQUE(batch, digest)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_bom ON documents(batch) WHERE role = 'bom';
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY, document TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created TEXT NOT NULL, content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS overlays (
    document TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    target TEXT NOT NULL, changes TEXT NOT NULL, PRIMARY KEY (document, target)
);
CREATE TABLE IF NOT EXISTS verification_runs (
    id INTEGER PRIMARY KEY, batch TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    created TEXT NOT NULL, inputs TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
    batch TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE, issue TEXT NOT NULL,
    fingerprint TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created TEXT NOT NULL,
    PRIMARY KEY (batch, issue, fingerprint)
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, batch TEXT, action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'Local operator',
    created TEXT NOT NULL, data TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path.touch(mode=0o600, exist_ok=True)
        with self.connection(initialize=True) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self, write=False, initialize=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA secure_delete = ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            elif not initialize:
                db.execute("BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def event(db, batch, action, data):
    db.execute("INSERT INTO audit (batch, action, created, data) VALUES (?, ?, ?, ?)",
               (batch, action, now(), json.dumps(data, ensure_ascii=False)))


def apply_overlay(document, target, changes):
    if target == "@terms":
        for field, value in changes.get("values", {}).items():
            document["terms"][field] = value
        document["term_anchors"].update(changes.get("anchors", {}))
        document["corrected_terms"] = list(changes.get("values", {}))
        return
    line = next((line for line in document["lines"] if line["id"] == target), None)
    if line is None and changes.get("new"):
        line = {"id": target, "fields": {}, "anchors": {}, "original": ""}
        document["lines"].append(line)
    if line is not None:
        line["fields"].update(changes.get("values", {}))
        line["anchors"].update(changes.get("anchors", {}))
        line.update({key: changes[key] for key in ("match", "factor") if key in changes})


def documents(db, batch):
    rows = db.execute("""
        SELECT d.id, d.batch, d.role, d.name, d.kind, d.supplier, d.coverage, d.digest,
               d.status, d.error, s.content, s.created AS extracted_at
        FROM documents d LEFT JOIN snapshots s ON s.id = d.snapshot_id
        WHERE d.batch = ? ORDER BY d.role, d.rowid
    """, (batch,))
    docs = []
    for row in rows:
        doc = dict(row)
        stored = doc.pop("content")
        doc.update(json.loads(stored) if stored else {
            "sources": [], "lines": [], "terms": {}, "term_anchors": {}, "term_mentions": {}
        })
        for overlay in db.execute("SELECT target, changes FROM overlays WHERE document = ?", (doc["id"],)):
            apply_overlay(doc, overlay["target"], json.loads(overlay["changes"]))
        docs.append(doc)
    return docs


def attach_reviews(db, batch, issues):
    for issue in issues:
        review = db.execute("""
            SELECT status, note, created FROM reviews WHERE batch = ? AND issue = ? AND fingerprint = ?
        """, (batch, issue["id"], issue["fingerprint"])).fetchone()
        issue["review"] = dict(review) if review else {"status": "open", "note": "", "created": None}
    return issues
