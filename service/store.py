"""SQLite transaction boundaries and views over immutable source data."""
import json
import re
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
DROP INDEX IF EXISTS one_bom;
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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
            for column, declaration in {
                "intake": "TEXT NOT NULL DEFAULT 'legacy'",
                "revises": "TEXT REFERENCES documents(id) ON DELETE SET NULL",
                "processing_step": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if column not in columns:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {column} {declaration}")

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


def apply_fields(values, anchors, changes):
    anchors.update(changes.get("anchors", {}))
    for field, value in changes.get("values", {}).items():
        anchor = anchors.get(field)
        if anchor and value != values.get(field):
            numeric = field in {"quantity", "unit_price", "amount", "subtotal", "total", "freight", "vat", "discount"}
            left = r"(?<!\w)(?<!\d[.,])" if numeric else r"(?<!\w)"
            right = r"(?!\w|[.,]\d)" if numeric else r"(?!\w)"
            if not value or not re.search(left + re.escape(value) + right, anchor["text"], re.I):
                anchors[field] = {**anchor, "weak": True}
        values[field] = value


def apply_overlay(document, target, changes):
    if target == "@terms":
        apply_fields(document["terms"], document["term_anchors"], changes)
        document["corrected_terms"] = list(changes.get("values", {}))
        return
    line = next((line for line in document["lines"] if line["id"] == target), None)
    if line is None and changes.get("new"):
        line = {"id": target, "fields": {}, "anchors": {}, "original": ""}
        document["lines"].append(line)
    if line is not None:
        apply_fields(line["fields"], line["anchors"], changes)
        line.update({key: changes[key] for key in ("match", "factor", "excluded") if key in changes})


def documents(db, batch):
    rows = db.execute("""
        SELECT d.id, d.batch, d.role, d.name, d.kind, d.supplier, d.coverage, d.digest,
               d.status, d.error, d.intake, d.revises, d.processing_step,
               d.snapshot_id, s.content, s.created AS extracted_at
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
        if doc["role"] == "quote" and doc["intake"] == "auto" and doc["terms"].get("supplier"):
            doc["supplier"] = doc["terms"]["supplier"]
        docs.append(doc)
    from service.verification import summary_quote

    superseded = {doc["revises"]: doc["id"] for doc in docs if doc["revises"] and doc["role"] == "quote"
                  and doc["status"] in {"ready", "needs_review"}
                  and (any(not line.get("excluded") for line in doc["lines"]) or summary_quote(doc))}
    for doc in docs:
        doc["superseded_by"] = superseded.get(doc["id"])
    return docs


def attach_reviews(db, batch, issues):
    for issue in issues:
        review = db.execute("""
            SELECT status, note, created FROM reviews WHERE batch = ? AND issue = ? AND fingerprint = ?
        """, (batch, issue["id"], issue["fingerprint"])).fetchone()
        issue["review"] = dict(review) if review else {"status": "open", "note": "", "created": None}
    return issues
