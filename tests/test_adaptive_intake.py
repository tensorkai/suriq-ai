"""Synthetic v2 intake scenarios exercised through the local API."""
from fastapi.testclient import TestClient

from service.api import create_app
from tests.fixtures import BOM, OFFER, TERMS, pdf, xlsx
from tests.test_acceptance import create, ok


def add(client, batch, name, content, **data):
    return client.post(f"/batches/{batch}/documents", files={"file": (name, content)}, data=data)


def test_revision_keeps_original_and_excludes_it_only_after_acceptance(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.xlsx", xlsx([BOM])))
        old_file = xlsx([OFFER], ["Supplier: Foundry", *TERMS])
        old = ok(add(client, batch, "offer.xlsx", old_file))["id"]
        revised = xlsx([[*OFFER[:4], "101", "8080"]], ["Supplier: Foundry", "Subtotal: 8080"])
        new = ok(add(client, batch, "revised.xlsx", revised, revises=old))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        docs = {doc["id"]: doc for doc in view["documents"]}
        assert docs[old]["superseded_by"] == new
        assert docs[new]["revises"] == old
        assert client.get(f"/documents/{old}/original").content == old_file
        assert add(client, batch, "copy.xlsx", old_file).status_code == 409
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        assert [row["document"] for row in ok(client.post(f"/batches/{batch}/verify"))["comparison"]] == [new]
        assert add(client, batch, "legacy.xls", b"unsupported legacy workbook").status_code == 415
        corrupt = b"%PDF-1.7\ntruncated synthetic file"
        bad = ok(add(client, batch, "corrupt.pdf", corrupt))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        assert next(doc for doc in view["documents"] if doc["id"] == bad)["status"] == "failed"
        assert client.get(f"/documents/{bad}/original").content == corrupt


def test_mixed_and_unsupported_documents_block_until_review(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.pdf", pdf([BOM])))
        quote = ok(add(client, batch, "offer.xlsx", xlsx([OFFER], ["Supplier: Foundry"])))["id"]
        mixed = ok(add(client, batch, "packet.pdf", pdf([OFFER], terms=[
            "Quotation number: 122", "Bill of materials", "Supplier: Other"
        ])))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        docs = {doc["id"]: doc for doc in view["documents"]}
        assert docs[quote]["role"] == "quote"
        assert docs[mixed]["role"] == "mixed"
        assert client.post(f"/batches/{batch}/verify").status_code == 409
        view = ok(client.patch(f"/documents/{mixed}/role", data={
            "role": "quote", "supplier": "Other", "reason": "BOM is an attachment to the offer.",
        }))
        assert next(doc for doc in view["documents"] if doc["id"] == mixed)["intake"] == "override"
        assert view["history"][0]["data"]["reason"] == "BOM is an attachment to the offer."


def test_merged_reordered_multisheet_workbook_and_docx_evidence(tmp_path):
    from io import BytesIO
    import shutil

    from openpyxl import Workbook
    from tests.fixtures import HEADERS, docx

    workbook = Workbook()
    workbook.remove(workbook.active)
    for index, name in enumerate(("Structural", "Finishes"), 1):
        sheet = workbook.create_sheet(name)
        if index == 1:
            sheet.append(["Construction schedule"])
            sheet.merge_cells("A1:D1")
            sheet.append(["U/M", "Material particulars", "Required qty", "Item ID"])
        else:
            sheet.append(["Unit", "Construction schedule"])
            sheet.merge_cells("A1:A2")
            sheet.merge_cells("B1:D1")
            sheet.append([None, "Material particulars", "Required qty", "Item ID"])
        sheet.append(["pcs", BOM[1], 80, f"B-01{index + 3}"])
    output = BytesIO()
    workbook.save(output)
    word = docx(["Supplier quotation", "Supplier: Foundry", "Valid until: 2027-01-15"], [HEADERS, OFFER])
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "supplier offer.xlsx", output.getvalue()))
        key = ok(add(client, batch, "schedule.docx", word))["id"]
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        baseline, quote = result["documents"]
        assert baseline["role"] == "bom"
        assert [(line["anchors"]["quantity"]["source"], line["anchors"]["quantity"]["cells"])
                for line in baseline["lines"]] == [("Structural", ["C3"]), ("Finishes", ["C3"])]
        assert [line["anchors"]["unit"]["cells"] for line in baseline["lines"]] == [["A3"], ["A3"]]
        assert quote["terms"]["validity"] == "2027-01-15"
        assert quote["term_anchors"]["validity"]["source"] == "Paragraph 3"
        issue = next(item for item in result["issues"] if item["rule"] == "dimension")
        assert any(a["source"] == "Table 1" and a["cells"] == ["B2"] and not a.get("boxes") for a in issue["evidence"])
        if shutil.which("soffice"):
            preview = client.get(f"/documents/{key}/rendered")
            assert preview.status_code == 200, preview.text
            assert preview.content.startswith(b"%PDF-")


def test_continued_pdf_columns_and_footnotes(tmp_path):
    from io import BytesIO

    from reportlab.pdfgen.canvas import Canvas
    from tests.fixtures import COLUMNS, HEADERS

    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(40, 800, "Foundry Hardware Inc.")
    for y, row in ((725, HEADERS), (706, OFFER)):
        for x, value in zip(COLUMNS, row):
            canvas.drawString(x, y, value)
    canvas.showPage()
    for x, value in zip(COLUMNS, ["B-015", "Anchor bolt", "10", "pcs", "12", "120"]):
        canvas.drawString(x, 725, value)
    canvas.drawString(40, 90, "Supplier: Foundry Hardware Inc.")
    canvas.drawString(40, 65, "Valid until: 2027-04-12")
    canvas.showPage()
    canvas.save()
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "no title.pdf", output.getvalue()))
        quote, = ok(client.get(f"/batches/{batch}"))["documents"]
        assert quote["role"] == "quote"
        assert [row["anchors"]["amount"]["source"] for row in quote["lines"]] == [1, 2]
        assert quote["term_anchors"]["validity"]["source"] == 2


def test_blank_and_invoice_do_not_create_a_baseline(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "offer.xlsx", xlsx([OFFER], ["Supplier: Foundry", *TERMS])))
        ok(add(client, batch, "blank.xlsx", xlsx([])))
        ok(add(client, batch, "invoice.pdf", pdf([OFFER], page=1, pages=1, terms=["Tax invoice #345", *TERMS])))
        roles = {doc["name"]: doc["role"] for doc in ok(client.get(f"/batches/{batch}"))["documents"]}
        assert roles == {"offer.xlsx": "quote", "blank.xlsx": "unknown", "invoice.pdf": "unsupported"}
        assert client.post(f"/batches/{batch}/verify").status_code == 409


def test_distinct_spec_values_make_distinct_issues(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.xlsx", xlsx([BOM])))
        ok(add(client, batch, "first.pdf", pdf([OFFER], terms=["Supplier: A", *TERMS])))
        ok(add(client, batch, "second.pdf", pdf([
            [OFFER[0], OFFER[1].replace("12 mm", "10 mm"), *OFFER[2:]]
        ], terms=["Supplier: B", *TERMS])))
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        issues = [issue for issue in ok(client.post(f"/batches/{batch}/verify"))["issues"] if issue["rule"] == "dimension"]
        assert len(issues) == 2
        assert all(len({a["document"] for a in issue["evidence"]}) == 2 for issue in issues)


def test_local_model_schema_grounding_and_invalid_result(tmp_path, monkeypatch):
    import json

    from service import ollama

    monkeypatch.setenv("SURIQ_ENABLE_OLLAMA", "1")
    invalid = None
    images_seen = False

    def local_response(path, body=None, **_):
        nonlocal images_seen
        if path == "/api/tags":
            return {"models": [{"name": ollama.model_name(), "digest": "test-fixture-digest"}]}
        assert path == "/api/chat" and body["format"]["type"] == "object"
        message = body["messages"][-1]
        images_seen = images_seen or bool(message.get("images"))
        elements = json.loads(message["content"])["elements"]
        offer = next(item for item in elements if "Anchor bolts" in item["text"])
        role = next(item for item in elements if "Quotation" in item["text"])
        citation = {"element": offer["id"], "value": "50"}
        if invalid == "missing":
            citation["element"] = "nonexistent"
        if invalid == "fabricated":
            citation["value"] = "500"
        if invalid == "box":
            citation["box"] = [0.1, 0.1, 0.2, 0.2]
        result = {
            "role": "quote", "rationale": "Supplier offer", "evidence": [role["id"]],
            "rows": [{"origin": offer["id"], "fields": [
                {"field": "material", "element": offer["id"], "value": "Anchor bolts"},
                {"field": "quantity", **citation},
                {"field": "unit", "element": offer["id"], "value": "pieces"},
                {"field": "unit_price", "element": offer["id"], "value": "20"},
            ]}],
            "terms": [],
        }
        return {"message": {"content": "not JSON" if invalid == "json" else json.dumps(result)}}

    monkeypatch.setattr(ollama, "request_json", local_response)
    from io import BytesIO
    from reportlab.pdfgen.canvas import Canvas

    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(40, 800, "Quotation")
    canvas.drawString(40, 780, "Supply schedule and extended rates")
    canvas.drawString(40, 760, "Quantity booked 50 | Anchor bolts | 20 per pieces")
    canvas.showPage()
    canvas.save()
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "unfamiliar.pdf", output.getvalue()))
        quote, = ok(client.get(f"/batches/{batch}"))["documents"]
        assert quote["role"] == "quote" and quote["status"] == "needs_review"
        assert quote["lines"][0]["fields"]["quantity"] == "50"
        anchor = quote["lines"][0]["anchors"]["quantity"]
        assert anchor["boxes"] and anchor["text"] == "50" and anchor["weak"]
        assert quote["provenance"]["model_digest"] == "test-fixture-digest" and images_seen
        for invalid in ("missing", "fabricated", "box", "json"):
            batch = create(client)
            ok(add(client, batch, "invalid.pdf", output.getvalue()))
            bad, = ok(client.get(f"/batches/{batch}"))["documents"]
            assert bad["provenance"]["inference"] == "needs_review"
            assert bad["role"] == "unknown" and not bad["lines"]
            assert client.post(f"/batches/{batch}/verify").status_code == 409


def test_ollama_unavailable_retains_original_and_allows_retry(tmp_path, monkeypatch):
    from urllib.error import URLError

    from service import ollama

    def fail(*_args, **_kwargs):
        raise URLError("unavailable")

    monkeypatch.setenv("SURIQ_ENABLE_OLLAMA", "1")
    monkeypatch.setattr(ollama, "request_json", fail)
    blob = xlsx([OFFER])  # Priced rows without commercial context are ambiguous.
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        key = ok(add(client, batch, "unidentified.xlsx", blob))["id"]
        result, = ok(client.get(f"/batches/{batch}"))["documents"]
        assert result["provenance"]["inference"] == "failed"
        assert result["role"] == "unknown" and result["lines"]
        assert client.get(f"/documents/{key}/original").content == blob
        ok(client.post(f"/documents/{key}/retry"))
        view = ok(client.get(f"/batches/{batch}"))
        assert len([entry for entry in view["history"] if entry["action"] == "extract_document"]) == 2


def test_priced_estimate_and_exceptional_bom_override(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "first.xlsx", xlsx([BOM])))
        estimate = ok(add(client, batch, "price sheet.xlsx", xlsx([
            [BOM[0], "Deformed bar 12 mm Grade 60", *BOM[2:4], "100", "8000"]
        ], ["Bill of materials: Project estimate", "Supplier: Foundry", "VAT: Inclusive", "Freight: 500"])))["id"]
        blocked = client.post(f"/batches/{batch}/verify")
        assert blocked.status_code == 409 and "Multiple BOMs" in blocked.json()["detail"]
        state = ok(client.patch(f"/documents/{estimate}/role", data={
            "role": "quote", "supplier": "Foundry", "reason": "Confirmed this is a supplier offer.",
        }))
        doc = next(doc for doc in state["documents"] if doc["id"] == estimate)
        assert doc["classification"]["role"] == "bom" and doc["role"] == "quote"
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        assert [row["document"] for row in ok(client.post(f"/batches/{batch}/verify"))["comparison"]] == [estimate]


def test_scanned_unfamiliar_layout_keeps_exact_ocr_or_requests_review(tmp_path):
    import os
    import shutil
    from io import BytesIO

    import pytest
    from reportlab.pdfgen.canvas import Canvas
    from tests.fixtures import scan

    if not shutil.which(os.getenv("SURIQ_TESSERACT", "tesseract")):
        pytest.skip("Tesseract is required for scanned PDF acceptance.")
    output = BytesIO()
    canvas = Canvas(output)
    canvas.setFont("Helvetica", 16)
    canvas.drawString(45, 780, "Supplier quotation")
    canvas.drawString(45, 750, "Supplier: Synthetic Foundry")
    canvas.drawString(255, 701, "Lot 1: Anchor bolts")
    canvas.drawString(50, 679, "Ordered pieces: 80")
    canvas.drawString(337, 650, "Price per piece: PHP 100")
    canvas.showPage()
    canvas.save()
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        key = ok(add(client, batch, "scan.pdf", scan(output.getvalue())))["id"]
        document, = ok(client.get(f"/batches/{batch}"))["documents"]
        assert document["sources"][0]["method"] == "ocr"
        assert document["status"] == "needs_review"
        assert "No structured rows" in document["error"]
        assert document["lines"] == []
        assert client.get(f"/documents/{key}/page/1").content.startswith(b"\x89PNG")


def test_mixed_reference_rows_can_be_excluded_with_audit(tmp_path):
    from tests.fixtures import HEADERS, docx

    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.xlsx", xlsx([BOM])))
        key = ok(add(client, batch, "packet.docx", docx(
            ["Quotation", "Bill of materials", "Supplier: Foundry"],
            [HEADERS, OFFER, BOM],
        )))["id"]
        data = ok(client.patch(f"/documents/{key}/role", data={
            "role": "quote", "supplier": "Foundry", "reason": "The last row is a reference copy.",
            "exclude_rows": "Table 1:3",
        }))
        assert data["history"][0]["data"]["excluded_rows"] == ["Table 1:3"]
        doc = next(doc for doc in data["documents"] if doc["id"] == key)
        assert len(doc["lines"]) == 2 and doc["lines"][1]["excluded"]
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        assert not any(issue["rule"] == "duplicate" for issue in result["issues"])
        assert client.get(f"/documents/{key}/original").content


def test_numeric_model_citations_require_complete_tokens():
    import pytest

    from service.adaptive import Citation, resolve
    from service.ollama import InferenceError

    elements = {"e1": (1, {"text": "1,500 at 20.50 | lot 120 | count 20", "source": 1}, None)}
    assert resolve(Citation(element="e1", value="20"), elements, numeric=True)[0] == 1
    labelled = {"e1": (1, {"text": "Price per piece: PHP 100", "source": 1}, None)}
    assert resolve(Citation(element="e1", value="price per piece: PHP 100"), labelled, numeric=True)[2] == "PHP 100"
    for value in ("500", "50"):
        with pytest.raises(InferenceError):
            resolve(Citation(element="e1", value=value), elements, numeric=True)


def test_scanned_correction_match_and_review_persist(tmp_path, monkeypatch):
    import os
    import shutil

    import pytest
    from service import extraction
    from tests.fixtures import scan

    if not shutil.which(os.getenv("SURIQ_TESSERACT", "tesseract")):
        pytest.skip("Tesseract is required for scanned PDF acceptance.")
    monkeypatch.setattr(extraction, "OCR_CONFIDENCE", 99)
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.xlsx", xlsx([BOM])))
        scanned = scan(pdf([OFFER], pages=1, terms=["Supplier: Foundry", "Quotation"]))
        key = ok(add(client, batch, "scan.pdf", scanned))["id"]
        # Low-confidence OCR can require type review independently of row review.
        data = ok(client.get(f"/batches/{batch}"))
        quote = next(doc for doc in data["documents"] if doc["id"] == key)
        if quote["role"] != "quote":
            ok(client.patch(f"/documents/{key}/role", data={
                "role": "quote", "supplier": "Foundry", "reason": "Checked the scanned offer title.",
            }))
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        quote_line = quote["lines"][0]
        bom_line = next(doc for doc in result["documents"] if doc["role"] == "bom")["lines"][0]
        dimension = quote_line["anchors"]["dimension"]
        x = min(box[0] for box in dimension["boxes"])
        y = min(box[1] for box in dimension["boxes"])
        w = max(box[0] + box[2] for box in dimension["boxes"]) - x
        h = max(box[1] + box[3] for box in dimension["boxes"]) - y
        result = ok(client.patch(f"/documents/{key}/line/{quote_line['id']}", json={
            "values": {"dimension": "12 mm"},
            "anchors": {"dimension": {"source": 1, "box": [x, y, w, h], "text": "12 mm"}},
            "match": bom_line["id"],
            "note": "Checked the reading directly against the scan.",
        }))
        assert result["matches"][f"{key}:{quote_line['id']}"]["method"] == "manual"
        issue = next(issue for issue in result["issues"] if issue["rule"] == "dimension")
        assert any(e["document"] == key and e["boxes"] and not e["weak"] for e in issue["evidence"])
        ok(client.patch(f"/batches/{batch}/issues/{issue['id']}", json={
            "status": "false_positive", "note": "Synthetic reviewer decision.",
        }))
        result = ok(client.get(f"/batches/{batch}"))
        assert next(item for item in result["issues"] if item["id"] == issue["id"])["review"]["status"] == "false_positive"
        assert any(event["action"] == "correct_line" for event in result["history"])


def test_line_total_error_and_ambiguous_vat_stay_separate(tmp_path):
    with TestClient(create_app(tmp_path / "db.sqlite")) as client:
        batch = create(client)
        ok(add(client, batch, "requirements.xlsx", xlsx([BOM])))
        ok(add(client, batch, "offer.xlsx", xlsx([[*BOM[:4], "PHP 100.00", "PHP 7999.99"]], [
            "Supplier: Foundry", "Tax: VAT inclusive/exclusive TBC", "Freight: Included",
        ])))
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        issue = next(item for item in result["issues"] if item["rule"] == "line_math")
        assert issue["confirmable"]
        assert {cell for anchor in issue["evidence"] for cell in anchor["cells"]} == {"C2", "E2", "F2"}
        assert result["comparison"][0]["tax_basis"] == "unknown"
        assert result["comparison"][0]["total"] is None


def test_hosted_reading_does_not_flag_repeats_labels_or_long_pages(tmp_path, monkeypatch):
    import json

    from service import meta
    from service.adaptive import WINDOW_ROWS, Interpretation, Material, TermField, reconcile

    # A full page of rows fits the schema the model must answer with.
    row = Material(origin="e1", fields=[{"field": "material", "element": "e1", "value": "Bar"}])
    Interpretation(role="quote", rationale="Quote", evidence=[], rows=[row] * WINDOW_ROWS, terms=[])
    # "Label: value" resolves to the parser's labeled value with its certain anchor.
    elements = {"e1": (1, {"text": "Freight: Included", "source": 1, "weak": False}, None)}
    _, terms = reconcile(Interpretation(role="unknown", rationale="Terms", evidence=[], rows=[], terms=[
        TermField(field="freight", element="e1", value="Freight: Included")]), elements, 1)
    assert [(field, value, anchor["weak"]) for field, value, anchor in terms] == [("freight", "Included", False)]

    def send(request, timeout):
        payload = json.loads(json.loads(request.data)["messages"][1]["content"][0]["text"])
        name = next((item for item in payload["elements"] if BOM[1] in item["text"]), None)
        answer = {"role": "unknown", "rationale": "No rows", "evidence": [], "terms": [], "rows": []} if not name else {
            "role": "bom", "rationale": "Material list", "evidence": [name["id"]], "terms": [], "rows": [{
            "origin": name["id"], "kind": "material", "fields": [
                {"field": "material", "element": name["id"], "value": BOM[1]},
                {"field": "description", "element": name["id"], "value": BOM[1]}]}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]}

    monkeypatch.setenv("SURIQ_AI_PROVIDER", "meta")
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    monkeypatch.setattr(meta, "send", send)
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        key = ok(add(client, batch, "BOM.pdf", pdf([BOM])))["id"]
        doc = next(item for item in ok(client.get(f"/batches/{batch}"))["documents"] if item["id"] == key)
        assert doc["provenance"]["inference"] == "processed"
        # The model repeated the parsed name; that repeat must not add an unconfirmed field.
        assert "description" not in doc["lines"][0]["fields"]
        assert not any(anchor.get("weak") for anchor in doc["lines"][0]["anchors"].values())
