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

from service.inference import provider, public_status
from service.extraction import (
    LINE_FIELDS, PARSER_VERSION, TERM_FIELDS, ExtractionError, extract, render_docx, render_page, run_command, validate_file,
)
from service.reports import report
from service.store import Store, apply_overlay, attach_reviews, documents, event, now
from service.suggestions import suggest
from service.verification import decimal_value, one_schedule, schedule_baseline, summary_quote, verify
from service.workbook_export import WorkbookExportError, annotated_workbook

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
    excluded: bool | None = None


class SourceSearch(Input):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


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
    data["ai"] = public_status()
    data["documents"] = documents(db, batch)
    for doc in data["documents"]:
        doc.pop("ocr_search", None)
        doc["summary_only"] = summary_quote(doc)
        doc["parser_update_available"] = (
            doc["kind"] == "pdf" and doc["status"] == "ready"
            and doc.get("provenance", {}).get("parser") != PARSER_VERSION
            and not db.execute("SELECT 1 FROM overlays WHERE document = ?", (doc["id"],)).fetchone()
        )
    data["verification_problem"], data["verification_action"] = verification_problem(
        active_documents(data["documents"]), data["scope_mode"], data["scope"]
    )
    data.update(json.loads(stored) if stored and not data["stale"] else {
        "issues": [], "comparison": [], "matches": {},
    })
    attach_reviews(db, batch, data["issues"])
    add_review_context(data["issues"], data["documents"], data["matches"])
    data["history"] = [
        {**dict(row), "data": json.loads(row["data"])} for row in db.execute("""
            SELECT id, action, actor, created, data FROM audit WHERE batch = ? ORDER BY id DESC LIMIT 150
        """, (batch,))
    ]
    return data


def active_documents(docs):
    return [{**doc, "has_excluded_rows": any(line.get("excluded") for line in doc["lines"]),
             "lines": [line for line in doc["lines"] if not line.get("excluded") and line.get("kind") != "service"]}
            for doc in docs if not doc["superseded_by"]]


def verification_problem(docs, scope_mode, scope):
    boms = [doc for doc in docs if doc["role"] == "bom"]
    quotes = [doc for doc in docs if doc["role"] == "quote"]
    if any(doc["status"] == "processing" for doc in docs):
        return "Wait for the remaining documents to finish reading.", None
    if failed := next((doc for doc in docs if doc["status"] == "failed"), None):
        return "A file could not be read. Try reading it again.", {"kind": "retry", "document": failed["id"]}
    if unsupported := next((doc for doc in docs if doc["role"] == "unsupported"), None):
        return "Remove unsupported procurement documents from this batch.", {"kind": "manage", "document": unsupported["id"]}
    if bundle := next((doc for doc in docs if doc.get("classification", {}).get("bundled_offers")), None):
        return "Download the separate supplier offers, remove the bundle, and upload the individual PDFs.", {
            "kind": "read", "document": bundle["id"],
        }
    if revision := next((doc for doc in docs if doc["revises"] and doc["role"] != "quote"), None):
        return "A revised offer must be classified as a quotation.", {"kind": "type", "document": revision["id"]}
    if unclear := next((doc for doc in docs if doc["role"] not in {"bom", "quote"}), None):
        return "Confirm unknown or mixed document types before verification.", {"kind": "type", "document": unclear["id"]}
    if len(boms) > 1:
        return ("Multiple BOMs detected. Use one baseline BOM per batch and keep different project sites in separate batches.",
                {"kind": "manage", "document": boms[1]["id"]})
    if len(boms) != 1 or not quotes:
        return "Verification requires one BOM and one to five quotations.", {"kind": "upload"}
    if len(quotes) > 5:
        return "Verification requires at most five quotations.", {"kind": "manage", "document": quotes[5]["id"]}
    if empty := next((doc for doc in [*boms, *quotes] if not doc["lines"]
                      and (doc["role"] == "bom" or (doc["intake"] != "legacy" and not summary_quote(doc)))), None):
        return ("The BOM needs material rows. Each quote needs itemized rows or a priced summary.",
                {"kind": "read", "document": empty["id"]})
    baseline = {line["id"] for line in boms[0]["lines"]}
    if scope_mode == "unknown" or (scope_mode == "selected" and (not scope or set(scope) - baseline)):
        return "Set the purchase scope using the current BOM rows.", {"kind": "scope", "document": boms[0]["id"]}
    if not one_schedule(boms[0]["lines"], scope_mode, scope):
        return "Select material rows from one supplier schedule and project site.", {"kind": "scope", "document": boms[0]["id"]}
    return "", None


def add_review_context(issues, docs, matches):
    # Derive viewer metadata on read so saved reviews retain their evidence fingerprints.
    if not issues:
        return
    baseline = {line["id"]: line["fields"] for doc in docs if doc["role"] == "bom" for line in doc["lines"]}
    fields = {(doc["id"], line["id"]): line["fields"] for doc in docs for line in doc["lines"]}
    quotes = {doc["id"] for doc in docs if doc["role"] == "quote"}
    for issue in issues:
        rule, subject, condition = issue["rule"], issue["subject"], issue["condition"]
        for anchor in issue["evidence"]:
            if not (anchor.get("boxes") or anchor.get("cells")):
                is_page_warning = rule == "extraction" and condition == anchor["source"]
                anchor["scope"] = "page" if is_page_warning else "document"
        if rule == "missing" and isinstance(subject, str) and subject in baseline:
            row = baseline[subject]
            issue["search_hint"] = row.get("code") or row.get("material") or row.get("description", "")
        if rule == "charges" and isinstance(subject, list) and condition in {"freight", "charges", "discount"}:
            issue["source_editors"] = [{"document": subject[0], "target": "terms", "field": condition}]
        if rule == "tax" and condition in {"unknown", "VAT exclusive"}:
            issue["source_editors"] = [{"document": subject, "target": "terms",
                                        "field": "tax" if condition == "unknown" else "vat"}]
        if rule == "delivery" and issue["review_required"]:
            offer_ids = {anchor["document"] for anchor in issue["evidence"]} & quotes
            if subject == "batch":
                editors = [{"document": key, "target": "terms", "field": "delivery"} for key in sorted(offer_ids)]
            else:
                row = baseline.get(subject, {})
                field = "delivery_date" if row.get("delivery_date") == condition[0] else "lead_time"
                editors = [{"document": key, "target": line, "field": field}
                           for key, line in fields if key in offer_ids and matches.get(f"{key}:{line}", {}).get("bom") == subject]
            if editors:
                issue["source_editors"] = editors
        if rule == "extraction" and condition == "quantity or unit":
            line = fields.get(tuple(subject), {})
            field = "quantity" if decimal_value(line.get("quantity", "")) is None else "unit"
            issue["source_editors"] = [{"document": subject[0], "target": subject[1], "field": field}]
        if rule == "extraction" and condition == "no rows":
            issue["source_editors"] = [{"document": subject, "target": "new", "field": "material"}]


def rerun(db, batch, *, explicit=False):
    batch_row = get_row(db, "batches", batch)
    if not explicit and not batch_row["verified_at"]:
        return
    docs = active_documents(documents(db, batch))
    problem, _ = verification_problem(docs, batch_row["scope_mode"], json.loads(batch_row["scope"]))
    if problem:
        if explicit:
            fail(409, problem)
        db.execute("UPDATE batches SET stale = 1 WHERE id = ?", (batch,))
        return
    result = verify(docs, batch_row["scope_mode"], json.loads(batch_row["scope"]))
    payload, stamp = json.dumps(result), now()
    sources = [{key: doc[key] for key in ("id", "snapshot_id", "role", "coverage")} for doc in docs]
    db.execute("INSERT INTO verification_runs (batch, created, inputs, result) VALUES (?, ?, ?, ?)", (
        batch, stamp, json.dumps({
            "scope_mode": batch_row["scope_mode"], "scope": json.loads(batch_row["scope"]),
            "documents": sources,
            "last_event": db.execute("SELECT MAX(id) FROM audit WHERE batch = ?", (batch,)).fetchone()[0],
        }), payload,
    ))
    db.execute("UPDATE batches SET result = ?, verified_at = ?, stale = 0 WHERE id = ?", (payload, stamp, batch))


def clear_baseline_selection(db, batch):
    db.execute("UPDATE batches SET scope_mode = 'unknown', scope = '[]' WHERE id = ?", (batch,))
    db.execute("""UPDATE overlays SET changes = json_remove(changes, '$.match', '$.factor')
        WHERE document IN (SELECT id FROM documents WHERE batch = ?)""", (batch,))


def link_anchor(doc, link):
    source = next((source for source in doc["sources"] if source["ref"] == link.source), None)
    if source is None:
        fail(422, "Select a source page or worksheet from this document.")
    if doc["kind"] == "pdf" and link.box and link.text:
        x, y, width, height = link.box
        if 0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y:
            return {"source": link.source, "boxes": [list(link.box)], "text": link.text, "weak": False}
    if doc["kind"] in {"xlsx", "docx"} and link.cell:
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
                ELSE 'needs_review' END, error = 'Extraction was interrupted. Retry this source.',
                processing_step = '' WHERE status = 'processing'""")
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
            doc = db.execute("SELECT original, kind, batch, role, intake, supplier FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if doc is None:
            return
        try:
            def interpreting():
                with store.connection(write=True) as db:
                    step = "Gemini interpretation" if provider() == "gemini" else "Local AI interpretation"
                    db.execute("UPDATE documents SET processing_step = ? WHERE id = ?", (step, doc_id))

            snapshot = extract(doc["original"], doc["kind"], on_inference=interpreting)
            classification = snapshot["classification"]
            error = snapshot["provenance"].get("message", "")
            if not snapshot["lines"] and not classification.get("summary_only"):
                error = error or "No structured rows recognized. Link and enter rows from the source."
            role_review = (classification["role"] in {"mixed", "unsupported"} and doc["intake"] != "override"
                           or doc["intake"] == "auto" and classification["role"] == "unknown")
            status = "needs_review" if (
                error or role_review or any(source["warning"] for source in snapshot["sources"])
                or any(anchor.get("weak") for anchor in snapshot["term_anchors"].values())
                or any(anchor.get("weak") for line in snapshot["lines"] for anchor in line["anchors"].values())
            ) else "ready"
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
            if snapshot is not None and (doc["intake"] == "auto" or role_review):
                role = snapshot["classification"]["role"]
                supplier = snapshot["terms"].get("supplier", "") if role == "quote" else ""
                db.execute("UPDATE documents SET role = ?, supplier = ? WHERE id = ?",
                           (role, supplier or ("Unidentified supplier " + doc_id[:6] if role == "quote" else ""), doc_id))
            db.execute("UPDATE documents SET snapshot_id = ?, status = ?, error = ?, processing_step = '' WHERE id = ?",
                       (snapshot_id, status, error, doc_id))
            event(db, doc["batch"], "extract_document", {"document": doc_id, "status": status,
                                                          "snapshot": snapshot_id, "message": error})
            if snapshot is not None or snapshot_id:
                db.execute("SAVEPOINT refresh_verification")
                try:
                    rerun(db, doc["batch"])
                except Exception:
                    db.execute("ROLLBACK TO refresh_verification")
                    LOG.exception("Reverification failed after extracting %s", doc_id)
                finally:
                    db.execute("RELEASE refresh_verification")

    @app.get("/health")
    def health():
        return {"status": "ok", "ai": public_status()}

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
               role: Annotated[Role | None, Form()] = None, supplier: Annotated[str, Form(max_length=120)] = "",
               coverage: Annotated[Coverage, Form()] = "unknown",
               revises: Annotated[str, Form(max_length=32)] = ""):
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
        mime = {
            "pdf": "application/pdf",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if file.content_type not in {None, "", "application/octet-stream", "application/zip", mime[kind]}:
            fail(415, "The uploaded media type conflicts with the file format.")
        digest = hashlib.sha256(content).hexdigest()
        key = uuid4().hex
        with store.connection(write=True) as db:
            get_row(db, "batches", batch, "id")
            current = documents(db, batch)
            active = active_documents(current)
            if revises:
                predecessor = next((doc for doc in active if doc["id"] == revises and doc["role"] == "quote"), None)
                if not predecessor or any(doc["revises"] == revises for doc in current):
                    fail(409, "Select a current quotation without an existing revision.")
                if role == "bom":
                    fail(422, "A quotation revision must also be a quotation.")
            elif len(active) >= 6 and not role:
                fail(409, "The batch has six source files. Resolve a conflict or upload a quotation revision.")
            if any(doc["digest"] == digest for doc in current):
                fail(409, "This original file has already been imported into the batch.")
            if role == "bom" and any(doc["role"] == "bom" for doc in current):
                fail(409, "A batch accepts exactly one BOM.")
            if role == "quote" and sum(doc["role"] == "quote" for doc in active) >= 5 and not revises:
                fail(409, "A batch accepts at most five quotations.")
            db.execute("""INSERT INTO documents
                (id, batch, role, name, kind, supplier, coverage, digest, original, status, intake, revises)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?)""",
                       (key, batch, role or "unknown", name, kind, supplier, coverage, digest, content,
                        "legacy" if role else "auto", revises or None))
            db.execute("UPDATE batches SET stale = 1 WHERE id = ? AND verified_at IS NOT NULL", (batch,))
            event(db, batch, "import_document", {"document": key, "name": name, "sha256": digest,
                                                  "role": role, "supplier": supplier, "coverage": coverage,
                                                  "revises": revises or None})
        tasks.add_task(process, key)
        return {"id": key}

    @app.patch("/documents/{key}/role")
    def correct_role(key: str, role: Role = Form(), supplier: str = Form(default="", max_length=120),
                     reason: str = Form(min_length=1, max_length=500),
                     exclude_rows: list[Key] = Form(default_factory=list, max_length=500)):
        with store.connection(write=True) as db:
            doc = get_row(db, "documents", key, "role, status, batch, supplier, revises")
            if not reason.strip():
                fail(422, "Explain why the document type is being corrected.")
            current = next(row for row in documents(db, doc["batch"]) if row["id"] == key)
            if current.get("classification", {}).get("bundled_offers"):
                fail(422, "Download the separate offers before assigning a quotation type.")
            if doc["role"] == "unsupported":
                fail(422, "Remove unsupported procurement documents from the batch.")
            if doc["revises"] and role != "quote":
                fail(422, "A quotation revision must also be a quotation.")
            if any(row["revises"] == key for row in documents(db, doc["batch"])) and role != "quote":
                fail(422, "This quotation is linked to a revision.")
            if doc["status"] not in {"ready", "needs_review"}:
                fail(409, "Extract the source before correcting its type.")
            if role == "quote" and not supplier.strip():
                fail(422, "Enter the supplier name for this quotation.")
            if exclude_rows:
                if set(exclude_rows) - {line["id"] for line in current["lines"]}:
                    fail(422, "Select material rows from this source.")
                db.executemany("""INSERT INTO overlays VALUES (?, ?, '{"excluded":true}')
                    ON CONFLICT(document, target) DO UPDATE SET changes = json_set(changes, '$.excluded', json('true'))
                    """, [(key, row) for row in sorted(set(exclude_rows))])
            db.execute("UPDATE documents SET role = ?, supplier = ?, intake = 'override' WHERE id = ?",
                       (role, supplier.strip() if role == "quote" else "", key))
            if doc["role"] != role and "bom" in {doc["role"], role}:
                clear_baseline_selection(db, doc["batch"])
            event(db, doc["batch"], "correct_role", {"document": key, "before": doc["role"], "after": role,
                                                     "previous_supplier": doc["supplier"],
                                                     "supplier": supplier.strip() if role == "quote" else "",
                                                     "reason": reason.strip(), "excluded_rows": sorted(set(exclude_rows))})
            rerun(db, doc["batch"])
            return load_batch(db, doc["batch"])

    @app.post("/documents/{key}/retry", status_code=202)
    def retry(key: str, tasks: BackgroundTasks):
        with store.connection(write=True) as db:
            doc = get_row(db, "documents", key, "batch, status, kind, snapshot_id")
            if doc["status"] not in {"failed", "needs_review"}:
                snapshot = db.execute("SELECT content FROM snapshots WHERE id = ?", (doc["snapshot_id"],)).fetchone()
                version = json.loads(snapshot["content"]).get("provenance", {}).get("parser") if snapshot else None
                if doc["status"] != "ready" or doc["kind"] != "pdf" or version == PARSER_VERSION:
                    fail(409, "Retry failed or flagged sources, or reprocess a PDF with an older parser.")
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
            if db.execute("SELECT 1 FROM documents WHERE revises = ?", (key,)).fetchone():
                fail(409, "This quotation has a revision. Remove the revision before deleting its original.")
            db.execute("DELETE FROM documents WHERE id = ?", (key,))
            if doc["role"] == "bom":
                clear_baseline_selection(db, doc["batch"])
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

    @app.get("/documents/{key}/offer/{first_page}")
    def download_offer(key: str, first_page: int):
        with store.connection() as db:
            doc = get_row(db, "documents", key, "original, kind, snapshot_id")
            stored = db.execute("SELECT content FROM snapshots WHERE id = ?", (doc["snapshot_id"],)).fetchone()
        if doc["kind"] != "pdf" or not stored:
            fail(404, "This supplier offer is unavailable.")
        bundles = json.loads(stored["content"]).get("classification", {}).get("bundled_offers", [])
        offer = next((item for item in bundles if item["pages"][0] == first_page), None)
        if not offer:
            fail(404, "This supplier offer is unavailable.")
        try:
            with TemporaryDirectory(prefix="suriq-offer-") as tmp:
                folder = Path(tmp)
                path = folder / "original.pdf"
                path.write_bytes(doc["original"])
                pages = []
                for page in offer["pages"]:
                    run_command(["pdfseparate", "-f", str(page), "-l", str(page), str(path), str(folder / "page-%d.pdf")])
                    pages.append(folder / f"page-{page}.pdf")
                if len(pages) == 1:
                    content = pages[0].read_bytes()
                else:
                    combined = folder / "offer.pdf"
                    run_command(["pdfunite", *map(str, pages), str(combined)])
                    content = combined.read_bytes()
        except (ExtractionError, OSError):
            fail(422, "The supplier pages could not be separated. Download the original to review them.")
        from urllib.parse import quote
        name = quote(f"{offer['supplier']} - offer.pdf", safe="")
        return Response(content, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    @app.get("/documents/{key}/rendered")
    def rendered_docx(key: str):
        with store.connection() as db:
            doc = get_row(db, "documents", key, "kind, original")
        if doc["kind"] != "docx":
            fail(404, "This source has no DOCX rendering.")
        try:
            pdf = render_docx(doc["original"])
        except ExtractionError as exc:
            fail(422, str(exc))
        return Response(pdf, media_type="application/pdf",
                        headers={"Content-Disposition": "inline; filename=preview.pdf"})

    @app.post("/documents/{key}/search")
    def search(key: str, data: SourceSearch):
        with store.connection() as db:
            doc = get_row(db, "documents", key, "kind, original, snapshot_id")
            stored = db.execute("SELECT content FROM snapshots WHERE id = ?", (doc["snapshot_id"],)).fetchone()
        snapshot = json.loads(stored["content"]) if stored else {}
        sources = snapshot.get("sources", [])
        if doc["kind"] == "pdf" and sources:
            try:
                with TemporaryDirectory(prefix="suriq-search-") as tmp:
                    path = Path(tmp) / "original.pdf"
                    path.write_bytes(doc["original"])
                    output = run_command(["pdftotext", "-layout", "-cropbox", str(path), "-"])
            except ExtractionError:
                fail(422, "Search text is unavailable for this PDF. Open the original to review it.")
            pages = output.decode("utf-8", "replace").split("\f")
            ocr = snapshot.get("ocr_search", {})
            legacy_scans = {str(source["ref"]) for source in sources
                            if source["method"] == "ocr" and str(source["ref"]) not in ocr}
            excerpts = {}
            if legacy_scans:
                anchors = [*snapshot.get("term_anchors", {}).values(),
                           *(anchor for line in snapshot.get("lines", []) for anchor in line["anchors"].values())]
                for anchor in anchors:
                    ref = str(anchor["source"])
                    if ref in legacy_scans and anchor.get("text"):
                        excerpts.setdefault(ref, []).append(anchor["text"])
            rows = []
            for index, source in enumerate(sources):
                ref = source["ref"]
                text = pages[index] if index < len(pages) else ""
                lines = text.splitlines() if text.strip() else ocr.get(str(ref), excerpts.get(str(ref), []))
                rows.append((ref, lines))
            partial = bool(legacy_scans)
        else:
            partial = False
            rows = [(source["ref"], [" | ".join(row["cells"].values()) for row in source.get("rows", [])])
                    for source in sources]
        query = re.compile(re.escape(" ".join(data.query.split())), re.IGNORECASE)
        searchable = sum(any(line.strip() for line in lines) for _, lines in rows)
        partial |= searchable < len(rows)
        matches = []
        for ref, lines in rows:
            for line in dict.fromkeys(lines):
                text = " ".join(line.split())
                match = query.search(text)
                if match:
                    start = max(0, match.start() - 80)
                    excerpt = text[start:match.end() + 100]
                    matches.append({"source": ref, "text": ("…" if start else "") + excerpt})
                    if len(matches) == 50:
                        return {"searchable": True, "partial": partial, "matches": matches, "limited": True}
        return {"searchable": bool(searchable), "partial": partial, "matches": matches, "limited": False}

    @app.get("/documents/{key}/thumbnail/{number}")
    def thumbnail(key: str, number: int):
        return pdf_preview(key, number, scale=180)

    @app.get("/documents/{key}/page/{number}")
    def page(key: str, number: int):
        return pdf_preview(key, number)

    def pdf_preview(key, number, *, scale=None):
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
                png = render_page(path, number, tmp, scale=scale).read_bytes()
        except ExtractionError as exc:
            fail(422, str(exc))
        return Response(png, media_type="image/png")

    @app.patch("/batches/{batch}/scope")
    def scope(batch: str, data: Scope):
        with store.connection(write=True) as db:
            before = get_row(db, "batches", batch, "scope, scope_mode")
            boms = [doc for doc in active_documents(documents(db, batch)) if doc["role"] == "bom"]
            if data.mode != "unknown" and len(boms) != 1:
                fail(409, "Resolve the BOM role before selecting purchase scope.")
            rows = sorted(set(data.rows)) if data.mode == "selected" else []
            if rows and set(rows) - {line["id"] for line in boms[0]["lines"]}:
                fail(422, "Purchase scope must reference material rows from this BOM.")
            if data.mode != "unknown" and not one_schedule(boms[0]["lines"], data.mode, rows):
                fail(422, "Select material rows from one supplier schedule and project site.")
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
            if data.excluded is not None and (not line or not data.note):
                fail(422, "Changing a material row's inclusion requires a reason.")
            if (data.match is not None or data.factor is not None) and (term or doc["role"] != "quote"):
                fail(422, "Manual matches and conversions apply to quotation lines.")
            if data.match:
                bom = next((doc for doc in active_documents(docs) if doc["role"] == "bom"), None)
                scope = get_row(db, "batches", batch, "scope_mode, scope")
                baseline = schedule_baseline(bom["lines"], scope["scope_mode"], json.loads(scope["scope"])) if bom else []
                if data.match not in {line["id"] for line in baseline}:
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
            if data.excluded is not None:
                changes["excluded"] = data.excluded
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
            scope = get_row(db, "batches", meta["batch"], "scope_mode, scope")
            material = [row for row in bom["lines"] if row["anchors"] and not row.get("excluded") and row.get("kind") != "service"]
            anchored = schedule_baseline(material, scope["scope_mode"], json.loads(scope["scope"]))[:80]
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

    @app.get("/documents/{key}/export/xlsx")
    def export_workbook(key: str):
        from urllib.parse import quote

        with store.connection() as db:
            doc = get_row(db, "documents", key, "batch, kind, name, original")
            if doc["kind"] != "xlsx":
                fail(404, "Excel export is available for XLSX sources.")
            view = load_batch(db, doc["batch"])
            if not view["verified_at"] or view["stale"]:
                fail(409, "Verify this batch before exporting.")
            if any(item["id"] == key and item["superseded_by"] for item in view["documents"]):
                fail(409, "Export the current workbook for this quotation.")
        try:
            content = annotated_workbook(doc["original"], view["issues"], key)
        except WorkbookExportError as exc:
            LOG.warning("Workbook export failed for %s", key, exc_info=True)
            fail(422, str(exc))
        name = quote(f"{Path(doc['name']).stem}-annotated.xlsx", safe="")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    return app


def app():
    return create_app(os.getenv("SURIQ_DB", ".suriq/workspace.sqlite"))
