"""Index quotation bundles without using an abstract as offer evidence."""
import hashlib
import json
import re
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from service.store import documents, event, now

ABSTRACT = re.compile(r"\babstract of (?:bids|quotations|canvass)\b", re.I)
QUOTE = re.compile(r"^(?:(?:supplier|price|sales)\s+)?(?:quotation|quote|price proposal)\b", re.I)
SUPPLIER = re.compile(r"^(?:supplier(?: name)?|vendor|quoted by|bidder|contractor)\s*:\s*(.+)$", re.I)
REFERENCE = re.compile(r"^(?:(?:quotation|quote|offer|bid)\s+(?:ref(?:erence)?\.?|no\.?|number)"
                       r"(?:\s*[:#]\s*|\s+)|reference\s*[:#]\s*)(.+)$", re.I)
DOCUMENT_NUMBER = re.compile(r"\bdocument\s+(?:no\.?|number)\s*[:#]?\s+([A-Z0-9][A-Z0-9._/-]+)$", re.I)
SCOPE = re.compile(r"^(?:purchase |material[s]? |offer |project )?scope\s*:\s*(.+)$", re.I)
CONTINUED = re.compile(r"\b(?:continued(?:\s+from\s+(?:the\s+)?previous\s+page)?\s*$|continuation\b"
                       r"|page\s+0*(?:[2-9]|[1-9]\d+)\s*(?:of|/)\s*\d+\b)", re.I)
MONEY = re.compile(r"(?:(PHP|USD|EUR|GBP|JPY|AUD|SGD|CNY|₱|\$|€|£|¥)\s*)?"
                   r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)", re.I)
SYMBOLS = {"₱": "PHP", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
ABSTRACT_COLUMNS = {
    "supplier": "supplier", "supplier name": "supplier", "bidder": "supplier", "name of bidder": "supplier",
    "vendor": "supplier", "reference": "reference", "quote reference": "reference",
    "quotation reference": "reference", "quotation no": "reference", "revision": "reference",
    "scope": "scope", "project scope": "scope", "materials scope": "scope",
    "materials total": "materials_total", "total materials": "materials_total",
    "total": "total", "project total": "total", "total quoted price": "total", "bid amount": "total",
    "price basis": "basis", "vat basis": "basis",
}


class BundleError(ValueError):
    pass


def normalized(value):
    return " ".join(value.casefold().strip(" .:").split())


def amount_key(value, currency=""):
    match = MONEY.fullmatch(value.strip())
    if not match or not (match[1] or currency):
        return None
    code = (match[1] or currency).upper()
    return SYMBOLS.get(code, code), Decimal(match[2].replace(",", ""))


def abstract_table(ref, rows):
    from service.extraction import word_anchor

    for index, row in enumerate(rows[:25]):
        columns = []
        offset = 0
        while offset < len(row):
            for length in range(min(4, len(row) - offset), 0, -1):
                name = normalized(" ".join(word[0] for word in row[offset:offset + length]))
                if field := ABSTRACT_COLUMNS.get(name):
                    columns.append((row[offset][1][0], field))
                    offset += length
                    break
            else:
                offset += 1
        fields = {field for _, field in columns}
        if "supplier" not in fields or not fields & {"materials_total", "total"}:
            continue
        entries = []
        for words in rows[index + 1:]:
            cells = {}
            for word in words:
                eligible = [field for start, field in columns if start - .025 <= word[1][0]]
                if eligible:
                    cells.setdefault(eligible[-1], []).append(word)
            if "supplier" not in cells:
                continue
            values = {field: " ".join(word[0] for word in group) for field, group in cells.items()}
            for field in ("materials_total", "total"):
                if MONEY.fullmatch(values.get(field, "")):
                    key_words = [word for name in ("supplier", "reference", "scope", "basis")
                                 for word in cells.get(name, [])]
                    entries.append({**values, "field": field, "value": values[field],
                                    "anchor": word_anchor(ref, cells[field]),
                                    "link_usable": not word_anchor(ref, key_words)["weak"]})
        return True, entries
    return False, []


def mark_page(source, rows):
    from service.extraction import word_anchor, words_in_span

    texts = [" ".join(word[0] for word in row) for row in rows]
    header = texts[:12]
    abstract = next((index for index, text in enumerate(header) if ABSTRACT.search(text)), None)
    supplier = source.get("offer_issuer", "")
    if not supplier:
        supplier = next((match[1] for text in header if (match := SUPPLIER.fullmatch(text))), "")
    quote = bool(source.get("offer_issuer") or any(QUOTE.search(text) for text in header))
    reference_match = next(((index, match) for index, text in enumerate(header)
                            if (match := REFERENCE.fullmatch(text) or (quote and DOCUMENT_NUMBER.search(text)))), None)
    reference = reference_match[1][1] if reference_match else ""
    scope = next((match[1] for text, row in zip(header, rows)
                  if (match := SCOPE.fullmatch(text)) and not word_anchor(source["ref"], row)["weak"]), "")
    continuation = any(CONTINUED.search(text) for text in [*header, *texts[-3:]])
    table, summary = abstract_table(source["ref"], rows) if abstract is not None else (False, [])
    heading = abstract is not None and (table or bool(ABSTRACT.match(header[abstract])))
    # A completed response on a canvass form needs its own attributable offer and reference.
    if heading and not (supplier and reference and quote and not table):
        kind = "ambiguous" if quote and supplier else "reference"
    elif supplier and (quote or reference):
        kind = "offer"
    elif continuation or reference:
        kind = "continuation"
    else:
        kind = "unassigned"
    skip_offer = heading and kind in {"reference", "ambiguous"}
    if source["method"] == "ocr" and source["warning"]:
        critical = [row for text, row in zip(header, rows) if (
            ABSTRACT.search(text) and heading or supplier and supplier in text or QUOTE.search(text)
            or REFERENCE.fullmatch(text) or quote and DOCUMENT_NUMBER.search(text)
        )]
        if kind in {"offer", "reference"} and any(word_anchor(source["ref"], row)["weak"] for row in critical):
            kind = "ambiguous"
    source["bundle_mark"] = {"kind": kind, "supplier": supplier, "reference": reference, "scope": scope,
                             "continued": continuation, "abstract": heading, "skip_offer": skip_offer}
    if reference_match and kind in {"offer", "continuation"}:
        index, match = reference_match
        source["bundle_mark"]["reference_anchor"] = word_anchor(
            source["ref"], words_in_span(rows[index], *match.span(1)))
    if summary:
        source["reference_rows"] = summary
    return kind


def propose_layout(snapshot):
    sources = snapshot["sources"]
    offers, references, pending = [], [], []
    current = None
    for source in sources:
        page = source["ref"]
        mark = source.get("bundle_mark", {})
        kind = mark.get("kind")
        if kind == "reference":
            references.append(page)
            current = None
        elif kind == "ambiguous":
            pending.append(page)
            current = None
        elif kind == "offer" or source.get("offer_issuer"):
            supplier = mark.get("supplier") or source["offer_issuer"]
            reference = mark.get("reference", "")
            same = current and normalized(supplier) == normalized(current["supplier"])
            if same and ((reference and normalized(reference) == normalized(current["reference"]))
                         or (mark.get("continued") and (not reference or not current["reference"]))):
                current["pages"].append(page)
                current["reference"] = current["reference"] or reference
            else:
                current = {"supplier": supplier, "reference": reference, "pages": [page]}
                offers.append(current)
        elif (current and kind == "continuation" and (mark.get("continued") or mark.get("reference"))
              and (not mark.get("supplier") or normalized(mark["supplier"]) == normalized(current["supplier"]))
              and (not mark.get("reference") or normalized(mark["reference"]) == normalized(current["reference"]))):
            current["pages"].append(page)
        else:
            pending.append(page)
            current = None
    suppliers = {normalized(offer["supplier"]) for offer in offers}
    references_by_supplier = {(normalized(offer["supplier"]), normalized(offer["reference"]))
                              for offer in offers if offer["reference"]}
    ambiguous = [source["bundle_mark"] for source in sources if source.get("bundle_mark", {}).get("kind") == "ambiguous"]
    starts_without_references = Counter(normalized(mark["supplier"]) for source in sources
                                       if (mark := source.get("bundle_mark", {})).get("kind") == "offer"
                                       and not mark["reference"] and not mark["continued"])
    if (len(suppliers) < 2 and len(references_by_supplier) < 2 and not references
            and not any(mark["abstract"] for mark in ambiguous)
            and not any(count > 1 for count in starts_without_references.values())
            and (not ambiguous or len(offers) + len(ambiguous) < 2)):
        return None
    duplicates = Counter((normalized(offer["supplier"]), normalized(offer["reference"])) for offer in offers)
    if any(count > 1 for count in duplicates.values()):
        # Repeated letterheads without a clear continuation need a reviewer to decide the boundaries.
        pending.extend(page for offer in offers
                       if duplicates[(normalized(offer["supplier"]), normalized(offer["reference"]))] > 1
                       for page in offer["pages"])
        offers = [offer for offer in offers
                  if duplicates[(normalized(offer["supplier"]), normalized(offer["reference"]))] == 1]
    return {"pages": [source["ref"] for source in sources], "offers": offers, "reference_pages": references,
            "pending_pages": sorted(pending), "excluded": [], "reason": "", "undo_revision": None}


def quote_snapshot(snapshot, offer, *, manual=False):
    from service.classification import classify
    from service.extraction import add_term

    pages = set(offer["pages"])
    child = {
        "sources": [source for source in snapshot["sources"] if source["ref"] in pages],
        "lines": [line for line in snapshot["lines"] if all(a["source"] in pages for a in line["anchors"].values())],
        "headings": [anchor for anchor in snapshot.get("bundle_headings", []) if anchor["source"] in pages],
        "term_mentions": {}, "terms": {}, "term_anchors": {},
        "provenance": snapshot["provenance"],
        "ocr_search": {page: text for page, text in snapshot.get("ocr_search", {}).items() if int(page) in pages},
    }
    for field, mentions in snapshot["term_mentions"].items():
        for mention in mentions:
            if mention["anchor"]["source"] in pages:
                add_term(child, (field, mention["value"], mention["anchor"]))
    child["classification"] = classify(child)
    child.pop("headings")
    child["classification"].update(role="quote", bundled_offers=[])
    if manual and not child["lines"] and any(
        child["term_anchors"].get(field) and not child["term_anchors"][field].get("weak")
        for field in ("materials_total", "total")
    ):
        child["classification"]["summary_only"] = True
    return child


def extract_pages(original, pages):
    from service.extraction import run_command

    with TemporaryDirectory(prefix="suriq-offer-") as tmp:
        folder = Path(tmp)
        path = folder / "original.pdf"
        path.write_bytes(original)
        separated = []
        for page in pages:
            run_command(["pdfseparate", "-f", str(page), "-l", str(page), str(path), str(folder / "page-%d.pdf")])
            separated.append(folder / f"page-{page}.pdf")
        if len(separated) == 1:
            return separated[0].read_bytes()
        combined = folder / "offer.pdf"
        run_command(["pdfunite", *map(str, separated), str(combined)])
        return combined.read_bytes()


def current_layout(db, parent, revision=None):
    if revision is None:
        row = db.execute("SELECT content FROM bundle_versions WHERE parent = ? ORDER BY revision DESC LIMIT 1",
                         (parent,)).fetchone()
    else:
        row = db.execute("SELECT content FROM bundle_versions WHERE parent = ? AND revision = ?",
                         (parent, revision)).fetchone()
    return json.loads(row[0]) if row else None


def available_slots(db, parent, batch):
    docs = documents(db, batch)
    own = {doc["id"] for doc in docs if doc["bundle_parent"] == parent}
    used = sum(doc["role"] == "quote" and doc["bundle_active"] and not doc["superseded_by"]
               and doc["id"] not in own and doc["revises"] not in own for doc in docs)
    return max(0, 5 - used)


def save_layout(db, parent, snapshot, layout, *, record=True, confirm_archive=False):
    key, batch = parent["id"], parent["batch"]
    live = layout["offers"] if not (layout["pending_pages"] or layout["reason"]) else []
    retained = {tuple(offer["pages"]) for offer in live}
    existing = {tuple(json.loads(row["bundle_pages"])): row for row in db.execute("""
        SELECT id, name, bundle_pages, bundle_active, intake, supplier FROM documents WHERE bundle_parent = ?
        """, (key,))}
    retired = [row for pages, row in existing.items() if row["bundle_active"] and pages not in retained]
    for row in retired:
        if db.execute("SELECT 1 FROM documents WHERE revises = ?", (row["id"],)).fetchone():
            raise BundleError("Resolve the quotation revision before moving its bundle pages.")
        if not confirm_archive and db.execute("SELECT 1 FROM overlays WHERE document = ?", (row["id"],)).fetchone():
            raise BundleError("Page assignments moved. Confirm that prior source corrections should stay with the earlier split.")
    db.executemany("UPDATE documents SET bundle_active = 0 WHERE id = ?", ((row["id"],) for row in retired))
    for offer in live:
        pages = tuple(offer["pages"])
        existing_child = existing.get(pages)
        child = existing_child["id"] if existing_child else uuid4().hex
        label = " · ".join(part for part in (offer["supplier"], offer["reference"]) if part)
        if not existing_child:
            from service.extraction import ExtractionError

            try:
                content = extract_pages(parent["original"], pages)
            except (ExtractionError, OSError) as exc:
                raise BundleError("Could not extract the assigned PDF pages. The bundle is unchanged.") from exc
            digest = hashlib.sha256(content).hexdigest()
            try:
                db.execute("""INSERT INTO documents
                    (id, batch, role, name, kind, supplier, coverage, digest, original, status, intake,
                     bundle_parent, bundle_pages)
                    VALUES (?, ?, 'quote', ?, 'pdf', ?, 'unknown', ?, ?, 'ready', 'bundle', ?, ?)""",
                           (child, batch, f"{label}.pdf", offer["supplier"], digest, content, key, json.dumps(pages)))
            except sqlite3.IntegrityError as exc:
                raise BundleError("A separated offer is already present in this batch.") from exc
        corrected = existing_child and db.execute("SELECT 1 FROM overlays WHERE document = ?", (child,)).fetchone()
        if not corrected:
            projected = quote_snapshot(snapshot, offer, manual=layout.get("manual", False))
            snapshot_id = db.execute("INSERT INTO snapshots (document, created, content) VALUES (?, ?, ?)",
                                     (child, now(), json.dumps(projected))).lastrowid
            status = "needs_review" if (any(source["warning"] for source in projected["sources"])
                                        or not projected["lines"] and not projected["classification"]["summary_only"]) else "ready"
            db.execute("UPDATE documents SET snapshot_id = ?, status = ? WHERE id = ?", (snapshot_id, status, child))
        supplier = (existing_child["supplier"] if existing_child and existing_child["intake"] == "override"
                    and existing_child["name"] == f"{label}.pdf" else offer["supplier"])
        db.execute("UPDATE documents SET bundle_active = 1, supplier = ?, name = ? WHERE id = ?",
                   (supplier, f"{label}.pdf", child))
    if record:
        previous = current_layout(db, key)
        layout = {**layout, "revision": (previous["revision"] if previous else 0) + 1}
        db.execute("INSERT INTO bundle_versions VALUES (?, ?, ?, ?)",
                   (key, layout["revision"], now(), json.dumps(layout)))
        event(db, batch, "bundle_layout", {"document": key, "revision": layout["revision"], "layout": layout})
    return layout


def compare_references(docs):
    quotes = [doc for doc in docs if doc["role"] == "quote"]
    links, findings = [], []
    for parent in (doc for doc in docs if doc["role"] == "bundle" and doc.get("bundle")):
        for source in parent["sources"]:
            if source["ref"] not in parent["bundle"]["reference_pages"]:
                continue
            for row in source.get("reference_rows", []):
                field = row["field"]
                if not row.get("reference") or not row.get("scope") or not row.get("link_usable", True) or row["anchor"].get("weak"):
                    continue
                candidates = [quote for quote in quotes
                              if normalized(quote["supplier"]) == normalized(row["supplier"])
                              and normalized(quote["terms"].get("reference", "")) == normalized(row["reference"])
                              and {normalized(scope) for page in quote["sources"]
                                   if (scope := page.get("bundle_mark", {}).get("scope"))} == {normalized(row["scope"])}
                              and normalized(quote["terms"].get("tax", "")) == normalized(row.get("basis", ""))]
                if len(candidates) != 1:
                    continue
                quote = candidates[0]
                anchor = quote["term_anchors"].get(field)
                if not anchor or anchor.get("weak"):
                    continue
                currency = quote["terms"].get("currency", "")
                reference = amount_key(row["value"])
                original = amount_key(quote["terms"].get(field, ""), currency)
                if not reference or not original or reference[0] != original[0]:
                    continue
                mentions = quote["term_mentions"].get(field, [])
                if field not in quote.get("corrected_terms", ()) and any(
                    amount_key(item["value"], currency or original[0]) != original for item in mentions
                ):
                    continue
                basis = "materials total" if field == "materials_total" else "project total"
                values = [row["value"], quote["terms"][field]]
                evidence = [{**row["anchor"], "document": parent["id"]}, {**anchor, "document": quote["id"]}]
                link = {"document": parent["id"], "source": source["ref"], "quote": quote["id"],
                        "supplier": quote["supplier"], "reference": row["reference"], "basis": basis,
                        "scope": row["scope"], "reference_value": values[0], "original_value": values[1],
                        "matches": reference[1] == original[1], "evidence": evidence}
                if link not in links:
                    links.append(link)
                if link["matches"]:
                    continue
                subject = [quote["id"], parent["id"], source["ref"]]
                condition = [row["reference"], row["scope"], basis, *values]
                key = hashlib.sha256(json.dumps(["reference_discrepancy", subject, condition]).encode()).hexdigest()[:20]
                finding = {
                    "id": key, "rule": "reference_discrepancy", "title": "Reference discrepancy", "severity": "Warning",
                    "note": f"The reference lists {values[0]} for the {basis}. The original quotation states {values[1]}.",
                    "subject": subject, "condition": condition, "review_required": False, "confirmable": True,
                    "evidence": evidence,
                    "detail": {"expected": values[0], "observed": values[1], "context": f"{quote['supplier']} · {basis}",
                               "reason": "The reference and original quotation state different amounts.",
                               "action": "Check the reference against the original quotation.",
                               "source_labels": {parent["id"]: "Reference summary", quote["id"]: "Original quotation"}},
                }
                finding["fingerprint"] = hashlib.sha256(json.dumps(finding, sort_keys=True).encode()).hexdigest()
                if all(item["id"] != key for item in findings):
                    findings.append(finding)
    return links, findings
