from fastapi.testclient import TestClient

from service.api import create_app
from tests.fixtures import BOM, OFFER, TERMS, pdf, xlsx


def ok(response):
    assert response.is_success, response.text
    return response.json()


def create(client):
    project = ok(client.post("/projects", json={"name": "Riverside"}))
    return ok(client.post(f"/projects/{project['id']}/batches", json={"name": "Steel round"}))["id"]


def upload(client, batch, name, blob, supplier="", coverage="full"):
    return ok(client.post(f"/batches/{batch}/documents",
                          data={"role": "quote" if supplier else "bom", "supplier": supplier,
                                "coverage": coverage}, files={"file": (name, blob)}))["id"]


def test_multi_page_issue_grouping_and_review(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        bom = upload(client, batch, "BOM.pdf", pdf([BOM], page=2))
        first = upload(client, batch, "A.pdf", pdf([OFFER], page=3, terms=TERMS), "A")
        second = upload(client, batch, "B.pdf", pdf([OFFER], page=1, terms=TERMS), "B")
        third = ["B-014", "Deformed bar 16 mm Grade 60", "60", "pcs", "100", "6000"]
        upload(client, batch, "C.pdf", pdf([third], page=4, pages=4), "C")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        assert [doc["status"] for doc in data["documents"]] == ["ready"] * 4
        dimensions = [issue for issue in data["issues"] if issue["rule"] == "dimension"]
        assert len(dimensions) == 1
        issue = dimensions[0]
        assert {(e["document"], e["source"]) for e in issue["evidence"]} == {
            (bom, 2), (first, 3), (second, 1)
        }
        assert all(e["boxes"] and "mm" in e["text"] for e in issue["evidence"])
        assert any(i["rule"] == "quantity" for i in data["issues"])
        image = client.get(f"/documents/{first}/page/3")
        assert image.is_success and image.content.startswith(b"\x89PNG")
        item = data["documents"][1]["lines"][0]["id"]
        data = ok(client.patch(f"/documents/{first}/line/{item}",
                               json={"values": {"dimension": "16 mm"}}))
        issue = next(i for i in data["issues"] if i["rule"] == "dimension")
        assert len(issue["evidence"]) == 2
        data = ok(client.patch(f"/batches/{batch}/issues/{issue['id']}",
                               json={"status": "false_positive", "note": "Supplier clarified."}))
        assert any(event["action"] == "correct_line" for event in data["history"])
        assert data["history"][0]["action"] == "review_issue"
        for ext in ("pdf", "csv"):
            response = client.get(f"/batches/{batch}/export/{ext}")
            assert response.is_success and response.content


def test_selected_scope_and_partial_offer(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        rows = [[f"M-{i:03}", f"Fastener type {i}", 1, "pcs", "", ""] for i in range(100)]
        upload(client, batch, "BOM.xlsx", xlsx(rows))
        offers = [[*row[:4], 25, 25] for row in rows[:10]]
        upload(client, batch, "Full.xlsx", xlsx(offers), "Full")
        upload(client, batch, "Partial.xlsx", xlsx(offers[:9]), "Partial", "partial")
        view = ok(client.get(f"/batches/{batch}"))
        required = [line["id"] for line in view["documents"][0]["lines"][:10]]
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "selected", "rows": required}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        assert len(data["scope"]) == 10
        assert not any(issue["rule"] == "missing" for issue in data["issues"])
        partial = next(row for row in data["comparison"] if row["supplier"] == "Partial")
        assert partial["items"][required[-1]]["state"] == "not offered"
        assert partial["total"] is None


def test_xlsx_and_scanned_pdf_have_precise_sources(tmp_path):
    import os
    import shutil

    import pytest

    from tests.fixtures import scan
    if not shutil.which(os.getenv("SURIQ_TESSERACT", "tesseract")):
        pytest.skip("Tesseract is required for scanned PDF acceptance.")
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([BOM]))
        upload(client, batch, "Scanned.pdf", scan(pdf([OFFER], pages=1)), "OCR Supplier")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        source = data["documents"][1]["sources"][0]
        assert source["method"] == "ocr"
        issue = next(item for item in data["issues"] if item["rule"] == "dimension")
        assert issue["evidence"][0]["cells"] == ["B2"]
        assert issue["evidence"][1]["source"] == 1
        assert issue["evidence"][1]["boxes"]
        assert all(0 <= number <= 1 for box in issue["evidence"][1]["boxes"] for number in box)


def test_documented_pack_size_and_missing_pack_detail(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([
            ["C-1", "Cement 40 kg/bag", 100, "bags", "", ""],
            ["G-1", "Grout 20 kg/bag", 10, "bags", "", ""],
        ]))
        upload(client, batch, "Offer.xlsx", xlsx([
            ["C-1", "Cement 50 kg/bag", 100, "bags", 100, 10000],
            ["G-1", "Grout", 10, "bags", 100, 1000],
        ]), "Supplier")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        pack = next(issue for issue in data["issues"] if issue["rule"] == "pack")
        unclear = next(issue for issue in data["issues"] if issue["rule"] == "unit")
        assert pack["severity"] == "Blocking"
        assert unclear["review_required"] and not unclear["confirmable"]


def test_comparability_and_arithmetic(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([BOM]))
        offer = [*BOM[:4], 100, 8000]
        upload(client, batch, "A.xlsx", xlsx([offer], [*TERMS, "Total: 8000"]), "Inclusive")
        upload(client, batch, "B.xlsx", xlsx([offer], [
            "Subtotal: 8000", "Tax: VAT exclusive 12%", "Freight: Included", "Total: 8960",
        ]), "Exclusive")
        upload(client, batch, "C.xlsx", xlsx([
            ["B-014", "Deformed bar 16 mm", 80, "pcs", 100, 8000],
        ], ["Subtotal: 7000", "Tax: VAT exclusive 12%"]), "Unclear", "partial")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        assert [(row["supplier"], row["total"]) for row in data["comparison"]] == [
            ("Inclusive", "8000.00"), ("Exclusive", "8960.00"), ("Unclear", None),
        ]
        assert "Freight is unclear" in data["comparison"][2]["reasons"]
        for rule in ("grade", "subtotal"):
            issue = next(issue for issue in data["issues"] if issue["rule"] == rule)
            assert issue["severity"] in ("Blocking", "Warning")
            assert all(anchor["cells"] for anchor in issue["evidence"])


def test_unreadable_page_and_unanchored_confirmation(tmp_path):
    from tests.fixtures import scan
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([BOM]))
        quote = upload(client, batch, "Blank.pdf", scan(), "Unreadable")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        assert data["documents"][1]["lines"] == []
        assert data["documents"][1]["status"] == "needs_review"
        assert client.get(f"/documents/{quote}/page/1").content.startswith(b"\x89PNG")
        issue = next(issue for issue in data["issues"] if issue["rule"] == "extraction")
        result = client.patch(f"/batches/{batch}/issues/{issue['id']}", json={"status": "confirmed"})
        assert result.status_code == 422
        assert data["comparison"][0]["total"] is None


def test_duplicate_and_invalid_uploads_and_persistence(tmp_path):
    location = tmp_path / "test.sqlite"
    blob = xlsx([BOM])
    with TestClient(create_app(location)) as client:
        batch = create(client)
        key = upload(client, batch, "BOM.xlsx", blob)
        route = f"/batches/{batch}/documents"
        assert client.post(route, data={"role": "bom"}, files={"file": ("again.xlsx", blob)}).status_code == 409
        assert client.post(route, data={"role": "bom"}, files={"file": ("wrong.pdf", blob)}).status_code == 415
        assert client.get("/projects", headers={"origin": "https://untrusted.example"}).status_code == 403
    with TestClient(create_app(location)) as client:
        assert client.get(f"/documents/{key}/original").content == blob
        data = ok(client.get(f"/batches/{batch}"))
        assert data["documents"][0]["status"] == "ready"
        project = data["project"]
        ok(client.delete(f"/projects/{project}"))
        assert client.get(f"/documents/{key}/original").status_code == 404


def test_unlinked_new_field_cannot_be_confirmed(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([BOM]))
        quote = upload(client, batch, "Offer.xlsx", xlsx([
            ["B-014", "Deformed bar 16 mm", 80, "pcs", 100, 8000],
        ]), "Supplier")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        view = ok(client.post(f"/batches/{batch}/verify"))
        line = view["documents"][1]["lines"][0]["id"]
        view = ok(client.patch(f"/documents/{quote}/line/{line}", json={"values": {"grade": "40"}}))
        issue = next(item for item in view["issues"] if item["rule"] == "grade")
        assert issue["review_required"] and not issue["confirmable"]


def test_sparse_oversize_workbook_is_rejected(tmp_path):
    from io import BytesIO
    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(xlsx([BOM])))
    workbook.active["BN505"] = "This value must never be silently ignored"
    output = BytesIO()
    workbook.save(output)
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "Oversize.xlsx", output.getvalue())
        data = ok(client.get(f"/batches/{batch}"))
        assert data["documents"][0]["status"] == "failed"
        assert "exceeds" in data["documents"][0]["error"]


def test_ambiguous_tax_and_large_decimal_product():
    from service.verification import decimal_value, money, tax_basis

    for value in ("VAT not included", "VAT inclusive/exclusive TBC", "Non-VAT or VAT inclusive"):
        assert tax_basis(value) == "unknown"
    assert money(decimal_value("999999999999999") ** 2).endswith(".00")


def test_rotated_pdf_geometry_uses_raster_orientation():
    from io import BytesIO
    from tempfile import TemporaryDirectory
    from pathlib import Path
    from struct import unpack

    from reportlab.pdfgen.canvas import Canvas
    from service.extraction import extract, render_page

    output = BytesIO()
    canvas = Canvas(output, pagesize=(600, 300))
    canvas.setPageRotation(90)
    canvas.drawString(40, 80, "Rotated original")
    canvas.showPage()
    canvas.save()
    blob = output.getvalue()
    source = extract(blob, "pdf")["sources"][0]
    with TemporaryDirectory() as tmp:
        original = Path(tmp) / "original.pdf"
        original.write_bytes(blob)
        width, height = unpack(">II", render_page(original, 1, tmp).read_bytes()[16:24])
    assert abs(source["width"] / source["height"] - width / height) < .001


def test_interrupted_extraction_preserves_original_and_can_retry(tmp_path):
    import sqlite3

    location = tmp_path / "test.sqlite"
    blob = xlsx([BOM])
    with TestClient(create_app(location)) as client:
        batch = create(client)
        key = upload(client, batch, "BOM.xlsx", blob)
    with sqlite3.connect(location) as db:
        db.execute("UPDATE documents SET status = 'processing' WHERE id = ?", (key,))
    with TestClient(create_app(location)) as client:
        view = ok(client.get(f"/batches/{batch}"))
        assert view["documents"][0]["status"] == "needs_review"
        assert client.get(f"/documents/{key}/original").content == blob
        ok(client.post(f"/documents/{key}/retry"))
        view = ok(client.get(f"/batches/{batch}"))
        assert view["documents"][0]["status"] == "ready"
        assert any(event["action"] == "retry_extraction" for event in view["history"])


def test_weak_ocr_creates_source_review(tmp_path, monkeypatch):
    import os
    import shutil

    import pytest

    from service import extraction
    from tests.fixtures import scan
    if not shutil.which(os.getenv("SURIQ_TESSERACT", "tesseract")):
        pytest.skip("Tesseract is required for OCR uncertainty acceptance.")
    monkeypatch.setattr(extraction, "OCR_CONFIDENCE", 99)
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", xlsx([BOM]))
        quote = upload(client, batch, "Scanned.pdf", scan(pdf([OFFER], pages=1)), "Scanned supplier")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        view = ok(client.post(f"/batches/{batch}/verify"))
        issue = next(item for item in view["issues"] if item["rule"] == "extraction")
        assert issue["review_required"] and not issue["confirmable"]
        assert issue["evidence"][0]["document"] == quote and issue["evidence"][0]["source"] == 1
        assert view["comparison"][0]["total"] is None
