"""Loopback document and procurement API."""
import hashlib
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.middleware.trustedhost import TrustedHostMiddleware

from service.extraction import (
    LINE_FIELDS, TERM_FIELDS, ExtractionError, extract, render_page, validate_file,
)
from service.reports import report
from service.store import Store, apply_overlay, attach_reviews, documents, event, now
from service.suggestions import suggest
from service.verification import decimal_value, verify

MAX_UPLOAD = int(os.getenv("SURIQ_MAX_UPLOAD_MB", "20")) * 1024 * 1024
LOG = logging.getLogger("suriq")
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Value = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Key = Annotated[str, StringConstraints(max_length=100)]
Coverage = Literal["full", "partial", "unknown"]
Role = Literal["bom", "quote"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Named(Input):
    name: Name


class Scope(Input):
    mode: Literal["unknown", "all", "selected"]
    rows: list[Key] = Field(default_factory=list, max_length=1000)


class SourceLink(Input):
    source: int | str
    cell: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,2}[1-9][0-9]{0,4}$")] | None = None
    box: tuple[float, float, float, float] | None = None
    text: Value = ""


class Correction(Input):
    values: dict[Key, Value] = Field(default_factory=dict, max_length=32)
    anchors: dict[Key, SourceLink] = Field(default_factory=dict, max_length=32)
    match: Key | None = None
    factor: Value | None = None
    note: Value = ""


class QuoteCoverage(Input):
    coverage: Coverage


class Review(Input):
    status: Literal["open", "confirmed", "false_positive", "clarification"]
    note: Value = ""
    fingerprint: Key | None = None


def fail(code, message):
    raise HTTPException(code, message)


def get_row(db, table, key, columns="*"):
    # Both SQL identifiers are internal literals; the ID is always bound.
    row = db.execute(f"SELECT {columns} FROM {table} WHERE id = ?", (key,)).fetchone()
    if row is None:
        fail(404, "The requested record was not found.")
    return row


def load_batch(db, batch):
    data = dict(get_row(db, "batches", batch))
    stored = data.pop("result")
    data["scope"] = json.loads(data["scope"])
    data["project_name"] = get_row(db, "projects", data["project"], "name")["name"]
    data["documents"] = documents(db, batch)
    data.update(json.loads(stored) if stored and not data["stale"] else {
        "issues": [], "comparison": [], "matches": {},
    })
    attach_reviews(db, batch, data["issues"])
    data["history"] = [
        {**dict(row), "data": json.loads(row["data"])} for row in db.execute("""
            SELECT id, action, actor, created, data FROM audit WHERE batch = ? ORDER BY id DESC LIMIT 150
        """, (batch,))
    ]
    return data


def rerun(db, batch, *, explicit=False):
    batch_row = get_row(db, "batches", batch)
    if not explicit and not batch_row["verified_at"]:
        return
    docs = documents(db, batch)
    ready = {"ready", "needs_review"}
    if not docs or docs[0]["role"] != "bom" or len(docs) < 2 or any(doc["status"] not in ready for doc in docs):
        if explicit:
            fail(409, "Upload one BOM and at least one quotation, then wait for extraction or retry failed files.")
        db.execute("UPDATE batches SET stale = 1 WHERE id = ?", (batch,))
        return
    result = verify(docs, batch_row["scope_mode"], json.loads(batch_row["scope"]))
    payload, stamp = json.dumps(result), now()
    sources = list(db.execute("SELECT id, snapshot_id, coverage FROM documents WHERE batch = ?", (batch,)))
    db.execute("INSERT INTO verification_runs (batch, created, inputs, result) VALUES (?, ?, ?, ?)", (
        batch, stamp, json.dumps({
            "scope_mode": batch_row["scope_mode"], "scope": json.loads(batch_row["scope"]),
            "documents": [dict(source) for source in sources],
            "last_event": db.execute("SELECT MAX(id) FROM audit WHERE batch = ?", (batch,)).fetchone()[0],
        }), payload,
    ))
    db.execute("UPDATE batches SET result = ?, verified_at = ?, stale = 0 WHERE id = ?", (payload, stamp, batch))


def link_anchor(doc, link):
    source = next((source for source in doc["sources"] if source["ref"] == link.source), None)
    if source is None:
        fail(422, "Select a source page or worksheet from this document.")
    if doc["kind"] == "pdf" and link.box and link.text:
        x, y, width, height = link.box
        if 0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y:
            return {"source": link.source, "boxes": [list(link.box)], "text": link.text, "weak": False}
    if doc["kind"] == "xlsx" and link.cell:
        row, column = int(re.search(r"\d+", link.cell)[0]), re.match(r"[A-Z]+", link.cell)[0]
        cell = next((entry["cells"].get(column) for entry in source["rows"] if entry["row"] == row), None)
        if cell is not None:
            return {"source": link.source, "cells": [link.cell], "text": cell, "weak": False}
    fail(422, "Select a populated cell or mark a PDF region and transcribe its text.")


def create_app(path):
    store = Store(path)
    extraction_slots = BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(_):
        with store.connection(write=True) as db:
            db.execute("""UPDATE documents SET status = CASE WHEN snapshot_id IS NULL THEN 'failed'
                ELSE 'needs_review' END, error = 'Extraction was interrupted. Retry this source.'
                WHERE status = 'processing'""")
        yield

    app = FastAPI(title="SuriQ AI local service", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        # Browser requests use the same-origin Next API. Reject direct cross-origin requests.
        if request.headers.get("origin") or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Use the local SuriQ workspace."}, status_code=403)
        content_length = request.headers.get("content-length")
        if request.headers.get("transfer-encoding") and not content_length:
            return JSONResponse({"detail": "A bounded request body is required."}, status_code=411)
        if content_length and (not content_length.isdecimal() or int(content_length) > MAX_UPLOAD + 65_536):
            return JSONResponse({"detail": "Request exceeds the upload limit."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def process(doc_id):
        with extraction_slots:
            process_source(doc_id)

    def process_source(doc_id):
        with store.connection() as db:
            doc = db.execute("SELECT original, kind, batch FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return
        try:
            snapshot = extract(doc["original"], doc["kind"])
            status = "needs_review" if (
                any(source["warning"] for source in snapshot["sources"]) or not snapshot["lines"]
            ) else "ready"
            error = "" if snapshot["lines"] else "No structured rows recognized. Link and enter rows from the source."
        except ExtractionError as exc:
            snapshot, status, error = None, "failed", str(exc)
        except Exception:
            LOG.exception("Extraction failed for %s", doc_id)
            snapshot, status, error = None, "failed", "Extraction failed. Check the source and retry."
        with store.connection(write=True) as db:
            current = db.execute("SELECT snapshot_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if current is None:
                return
            snapshot_id = current["snapshot_id"]
            if snapshot is not None:
                snapshot_id = db.execute("INSERT INTO snapshots (document, created, content) VALUES (?, ?, ?)",
                                         (doc_id, now(), json.dumps(snapshot))).lastrowid
            elif snapshot_id:
                status = "needs_review"
            db.execute("UPDATE documents SET snapshot_id = ?, status = ?, error = ? WHERE id = ?",
                       (snapshot_id, status, error, doc_id))
            event(db, doc["batch"], "extract_document", {"document": doc_id, "status": status,
                                                          "snapshot": snapshot_id, "message": error})
        if snapshot is not None or snapshot_id:
            try:
                with store.connection(write=True) as db:
                    if db.execute("SELECT 1 FROM batches WHERE id = ?", (doc["batch"],)).fetchone():
                        rerun(db, doc["batch"])
            except Exception:
                LOG.exception("Reverification failed after extracting %s", doc_id)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/projects")
    def projects():
        with store.connection() as db:
            result = []
            for row in db.execute("SELECT * FROM projects ORDER BY rowid DESC"):
                project = dict(row)
                project["batches"] = [dict(batch) for batch in db.execute("""
                    SELECT id, name, created, verified_at FROM batches WHERE project = ? ORDER BY rowid DESC
                """, (project["id"],))]
                result.append(project)
            return result

    @app.post("/projects", status_code=201)
    def create_project(data: Named):
        key = uuid4().hex
        with store.connection(write=True) as db:
            db.execute("INSERT INTO projects VALUES (?, ?, ?)", (key, data.name, now()))
        return {"id": key}

    @app.patch("/projects/{project}")
    def rename_project(project: str, data: Named):
        with store.connection(write=True) as db:
            get_row(db, "projects", project, "id")
            db.execute("UPDATE projects SET name = ? WHERE id = ?", (data.name, project))
        return {"id": project}

    @app.delete("/projects/{project}")
    def delete_project(project: str):
        with store.connection(write=True) as db:
            get_row(db, "projects", project, "id")
            for batch in db.execute("SELECT id FROM batches WHERE project = ?", (project,)).fetchall():
                db.execute("DELETE FROM audit WHERE batch = ?", (batch["id"],))
            db.execute("DELETE FROM projects WHERE id = ?", (project,))
            event(db, None, "delete_project", {"project": project})
        return {"id": project}

    @app.post("/projects/{project}/batches", status_code=201)
    def create_batch(project: str, data: Named):
        key = uuid4().hex
        with store.connection(write=True) as db:
            get_row(db, "projects", project, "id")
            db.execute("INSERT INTO batches (id, project, name, created) VALUES (?, ?, ?, ?)",
                       (key, project, data.name, now()))
            event(db, key, "create_batch", {"name": data.name})
        return {"id": key}

    @app.get("/batches/{batch}")
    def batch_detail(batch: str):
        with store.connection() as db:
            return load_batch(db, batch)

    @app.patch("/batches/{batch}")
    def rename_batch(batch: str, data: Named):
        with store.connection(write=True) as db:
            before = get_row(db, "batches", batch, "name")["name"]
            db.execute("UPDATE batches SET name = ? WHERE id = ?", (data.name, batch))
            event(db, batch, "rename_batch", {"before": before, "after": data.name})
            return load_batch(db, batch)

    @app.delete("/batches/{batch}")
    def delete_batch(batch: str):
        with store.connection(write=True) as db:
            get_row(db, "batches", batch, "id")
            db.execute("DELETE FROM audit WHERE batch = ?", (batch,))
            db.execute("DELETE FROM batches WHERE id = ?", (batch,))
            event(db, None, "delete_batch", {"batch": batch})
        return {"id": batch}

    @app.post("/batches/{batch}/documents", status_code=202)
    def upload(batch: str, tasks: BackgroundTasks, file: Annotated[UploadFile, File()],
               role: Annotated[Role, Form()], supplier: Annotated[str, Form(max_length=120)] = "",
               coverage: Annotated[Coverage, Form()] = "unknown"):
        supplier = supplier.strip()
        if role == "quote" and not supplier:
            fail(422, "Assign a supplier to the quotation.")
        content = file.file.read(MAX_UPLOAD + 1)
        if not content or len(content) > MAX_UPLOAD:
            fail(413, "The source is empty or exceeds the upload limit.")
        name = re.sub(r"[\x00-\x1f\x7f]", "", (file.filename or "").replace("\\", "/").split("/")[-1])
        if len(name) > 200:
            fail(422, "Use a filename with 200 characters or fewer.")
        try:
            kind = validate_file(name, content)
        except ExtractionError as exc:
            fail(415, str(exc))
        digest = hashlib.sha256(content).hexdigest()
        key = uuid4().hex
        with store.connection(write=True) as db:
            get_row(db, "batches", batch, "id")
            current = db.execute("SELECT role, digest, supplier FROM documents WHERE batch = ?", (batch,)).fetchall()
            if any(doc["digest"] == digest for doc in current):
                fail(409, "This original file has already been imported into the batch.")
            if role == "bom" and any(doc["role"] == "bom" for doc in current):
                fail(409, "A batch accepts exactly one BOM.")
            if role == "quote" and sum(doc["role"] == "quote" for doc in current) >= 5:
                fail(409, "A batch accepts at most five quotations.")
            db.execute("""INSERT INTO documents
                (id, batch, role, name, kind, supplier, coverage, digest, original, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing')""",
                       (key, batch, role, name, kind, supplier, coverage, digest, content))
            db.execute("UPDATE batches SET stale = 1 WHERE id = ? AND verified_at IS NOT NULL", (batch,))
            event(db, batch, "import_document", {"document": key, "name": name, "sha256": digest,
                                                  "role": role, "supplier": supplier, "coverage": coverage})
        tasks.add_task(process, key)
        return {"id": key}

    @app.post("/documents/{key}/retry", status_code=202)
    def retry(key: str, tasks: BackgroundTasks):
        with store.connection(write=True) as db:
            doc = get_row(db, "documents", key, "batch, status")
            if doc["status"] not in {"failed", "needs_review"}:
                fail(409, "Only failed or flagged sources can be retried.")
            if db.execute("SELECT 1 FROM overlays WHERE document = ?", (key,)).fetchone():
                fail(409, "This source has manual corrections. Retain them and use the staging grid.")
            db.execute("UPDATE documents SET status = 'processing', error = '' WHERE id = ?", (key,))
            db.execute("UPDATE batches SET stale = 1 WHERE id = ? AND verified_at IS NOT NULL", (doc["batch"],))
            event(db, doc["batch"], "retry_extraction", {"document": key})
        tasks.add_task(process, key)
        return {"id": key}

    @app.delete("/documents/{key}")
    def delete_document(key: str):
        with store.connection(write=True) as db:
            doc = get_row(db, "documents", key, "batch, role, name, digest")
            db.execute("DELETE FROM documents WHERE id = ?", (key,))
            if doc["role"] == "bom":
                db.execute("UPDATE batches SET scope_mode = 'unknown', scope = '[]' WHERE id = ?", (doc["batch"],))
                db.execute("UPDATE overlays SET changes = json_remove(changes, '$.match', '$.factor') "
                           "WHERE document IN (SELECT id FROM documents WHERE batch = ?) "
                           "AND (json_type(changes, '$.factor') IS NOT NULL OR json_extract(changes, '$.match') != '')",
                           (doc["batch"],))
            event(db, doc["batch"], "delete_document", {"document": key, "name": doc["name"], "sha256": doc["digest"]})
            rerun(db, doc["batch"])
            return load_batch(db, doc["batch"])

    @app.get("/documents/{key}/original")
    def original(key: str):
        from urllib.parse import quote
        with store.connection() as db:
            doc = get_row(db, "documents", key, "original, name")
        return Response(doc["original"], media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(doc['name'], safe='')}",
        })

    @app.get("/documents/{key}/page/{number}")
    def page(key: str, number: int):
        with store.connection() as db:
            doc = get_row(db, "documents", key, "kind, original, snapshot_id")
            stored = db.execute("SELECT content FROM snapshots WHERE id = ?", (doc["snapshot_id"],)).fetchone()
            if doc["kind"] != "pdf" or not stored or number not in {
                source["ref"] for source in json.loads(stored["content"])["sources"]
            }:
                fail(404, "The PDF page is unavailable.")
        try:
            with TemporaryDirectory(prefix="suriq-render-") as tmp:
                path = Path(tmp) / "original.pdf"
                path.write_bytes(doc["original"])
                png = render_page(path, number, tmp).read_bytes()
        except ExtractionError as exc:
            fail(422, str(exc))
        return Response(png, media_type="image/png")

    @app.patch("/batches/{batch}/scope")
    def scope(batch: str, data: Scope):
        with store.connection(write=True) as db:
            before = get_row(db, "batches", batch, "scope, scope_mode")
            bom = next((doc for doc in documents(db, batch) if doc["role"] == "bom"), None)
            rows = sorted(set(data.rows)) if data.mode == "selected" else []
            if rows and (not bom or set(rows) - {line["id"] for line in bom["lines"]}):
                fail(422, "Purchase scope must reference rows from this BOM.")
            db.execute("UPDATE batches SET scope = ?, scope_mode = ? WHERE id = ?",
                       (json.dumps(rows), data.mode, batch))
            event(db, batch, "set_scope", {"before": [before["scope_mode"], json.loads(before["scope"])],
                                            "after": [data.mode, rows]})
            rerun(db, batch)
            return load_batch(db, batch)

    @app.patch("/documents/{key}/coverage")
    def coverage(key: str, data: QuoteCoverage):
        with store.connection(write=True) as db:
            doc = get_row(db, "documents", key, "role, batch, coverage")
            if doc["role"] != "quote":
                fail(422, "Coverage applies to supplier quotations.")
            db.execute("UPDATE documents SET coverage = ? WHERE id = ?", (data.coverage, key))
            event(db, doc["batch"], "set_coverage", {"document": key, "before": doc["coverage"],
                                                     "after": data.coverage})
            rerun(db, doc["batch"])
            return load_batch(db, doc["batch"])

    def correction(key, target, data, *, new=False):
        with store.connection(write=True) as db:
            meta = get_row(db, "documents", key, "batch, status")
            if meta["status"] not in {"ready", "needs_review"}:
                fail(409, "Wait until the source is extracted.")
            batch = meta["batch"]
            docs = documents(db, batch)
            doc = next(doc for doc in docs if doc["id"] == key)
            term = target == "@terms"
            line = None if term else next((line for line in doc["lines"] if line["id"] == target), None)
            if not term and not line and not new:
                fail(404, "This material row was not found.")
            allowed = set(TERM_FIELDS if term else LINE_FIELDS)
            if (set(data.values) | set(data.anchors)) - allowed:
                fail(422, "One or more field names are unknown.")
            if new and (not (data.values.get("material") or data.values.get("description")) or not data.anchors):
                fail(422, "A new line needs a material description and a linked source.")
            if (data.match is not None or data.factor is not None) and (term or doc["role"] != "quote"):
                fail(422, "Manual matches and conversions apply to quotation lines.")
            if data.match:
                bom = next((doc for doc in docs if doc["role"] == "bom"), None)
                if not bom or data.match not in {line["id"] for line in bom["lines"]}:
                    fail(422, "Select an existing BOM line.")
            if data.factor and (not decimal_value(data.factor) or decimal_value(data.factor) <= 0 or not data.note):
                fail(422, "A positive conversion factor requires an explanation.")
            linked = {field: link_anchor(doc, link) for field, link in data.anchors.items()}
            existing = db.execute("SELECT changes FROM overlays WHERE document = ? AND target = ?",
                                  (key, target)).fetchone()
            changes = json.loads(existing["changes"]) if existing else {}
            before = {"values": doc["terms"], "anchors": doc["term_anchors"]} if term else line
            changes.setdefault("values", {}).update(data.values)
            changes.setdefault("anchors", {}).update(linked)
            if new:
                changes["new"] = True
                if not doc["lines"] and not any(source["warning"] for source in doc["sources"]):
                    db.execute("UPDATE documents SET status = 'ready', error = '' WHERE id = ?", (key,))
            if "match" in data.model_fields_set:
                if data.match is None:
                    changes.pop("match", None)
                else:
                    changes["match"] = data.match
            if data.factor is not None:
                changes["factor"] = data.factor
            db.execute("""INSERT INTO overlays VALUES (?, ?, ?) ON CONFLICT(document, target)
                DO UPDATE SET changes = excluded.changes""", (key, target, json.dumps(changes)))
            event(db, batch, "correct_terms" if term else "add_line" if new else "correct_line",
                  {"document": key, "target": target, "before": before, "after": changes, "note": data.note})
            rerun(db, batch)
            return load_batch(db, batch)

    @app.patch("/documents/{key}/line/{target}")
    def correct_line(key: str, target: str, data: Correction):
        if target == "@terms":
            fail(404, "This material row was not found.")
        return correction(key, target, data)

    @app.post("/documents/{key}/lines")
    def add_line(key: str, data: Correction):
        return correction(key, "manual-" + uuid4().hex[:12], data, new=True)

    @app.patch("/documents/{key}/terms")
    def correct_terms(key: str, data: Correction):
        return correction(key, "@terms", data)

    @app.post("/documents/{key}/line/{target}/suggest")
    def suggest_match(key: str, target: str):
        with store.connection() as db:
            meta = get_row(db, "documents", key, "batch, role")
            docs = documents(db, meta["batch"])
            doc = next(doc for doc in docs if doc["id"] == key)
            line = next((line for line in doc["lines"] if line["id"] == target), None)
            bom = next((doc for doc in docs if doc["role"] == "bom"), None)
            if doc["role"] != "quote" or not line or not bom:
                fail(422, "An extracted quotation line and a BOM are required.")
            anchored = [row for row in bom["lines"] if row["anchors"]][:80]
        ids, message = suggest(line, anchored)
        return {"candidates": ids, "message": message}

    @app.post("/batches/{batch}/verify")
    def run_verification(batch: str):
        with store.connection(write=True) as db:
            rerun(db, batch, explicit=True)
            event(db, batch, "verify_batch", {})
            return load_batch(db, batch)

    @app.patch("/batches/{batch}/issues/{issue_id}")
    def review_issue(batch: str, issue_id: str, data: Review):
        with store.connection(write=True) as db:
            view = load_batch(db, batch)
            issue = next((i for i in view["issues"] if i["id"] == issue_id), None)
            if not issue or (data.fingerprint and issue["fingerprint"] != data.fingerprint):
                fail(409, "This finding has changed. Refresh the batch before reviewing.")
            if data.status == "confirmed" and not issue["confirmable"]:
                fail(422, "Resolve the interpretation and link exact evidence before confirming.")
            stamp = now()
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?, ?, ?, ?, ?)",
                       (batch, issue_id, issue["fingerprint"], data.status, data.note, stamp))
            event(db, batch, "review_issue", {"issue": issue_id, "fingerprint": issue["fingerprint"],
                                               "before": issue["review"], "status": data.status,
                                               "note": data.note})
            return load_batch(db, batch)

    @app.get("/batches/{batch}/export/{extension}")
    def export(batch: str, extension: Literal["pdf", "csv"]):
        with store.connection() as db:
            view = load_batch(db, batch)
        if not view["verified_at"] or view["stale"]:
            fail(409, "Verify this batch before exporting.")
        content, mime = report(view, extension)
        return Response(content, media_type=mime, headers={
            "Content-Disposition": f'attachment; filename="suriq-verification.{extension}"',
        })

    return app


def app():
    return create_app(os.getenv("SURIQ_DB", ".suriq/workspace.sqlite"))
