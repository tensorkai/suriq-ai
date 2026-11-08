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


def test_annotated_workbook_preserves_original_and_marks_current_findings(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile

    from openpyxl import load_workbook
    from openpyxl.chart import BarChart, Reference
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Font, PatternFill

    workbook = load_workbook(BytesIO(xlsx([BOM])))
    materials = workbook["Materials"]
    materials["B2"].font = Font(bold=True, italic=True)
    materials["B2"].fill = PatternFill("solid", fgColor="FFCCEEDD")
    materials.conditional_formatting.add("B2:C2", FormulaRule(
        formula=["1"], fill=PatternFill("solid", fgColor="FFAACCFF")))
    materials.column_dimensions["B"].width = 41
    materials.freeze_panes = "B2"
    materials["G2"] = "=SUM(40,2)"
    materials["G2"].number_format = "0.00"
    materials["G3"] = "first[CR]second"
    notes = workbook.create_sheet("Layout")
    notes.merge_cells("A1:B1")
    notes["A1"] = "Calculation details"
    notes["A2"] = 42
    chart = BarChart()
    chart.add_data(Reference(notes, min_col=1, min_row=2))
    notes.add_chart(chart, "D4")
    output = BytesIO()
    workbook.save(output)
    source = BytesIO()
    with ZipFile(output) as archive, ZipFile(source, "w") as prepared:
        for name in archive.namelist():
            data = archive.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = data.replace(b"first[CR]second", b"first&#13;second")
            prepared.writestr(name, data)
    original = source.getvalue()

    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        bom = upload(client, batch, "BOM café.xlsx", original)
        offer = upload(client, batch, "Offer.pdf", pdf([OFFER], pages=1, terms=TERMS), "Steel supplier")
        route = f"/documents/{bom}/export/xlsx"
        assert client.get(f"/documents/{offer}/export/xlsx").status_code == 404
        assert client.get(route).status_code == 409
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        view = ok(client.post(f"/batches/{batch}/verify"))
        issue = next(item for item in view["issues"] if item["rule"] == "dimension")
        response = client.get(route)
        assert response.is_success, response.text
        assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert "BOM%20caf%C3%A9-annotated.xlsx" in response.headers["content-disposition"]
        exported = load_workbook(BytesIO(response.content))
        assert exported.sheetnames == ["Materials", "Layout"]
        sheet = exported["Materials"]
        assert sheet["B2"].value == BOM[1]
        assert sheet["B2"].font.bold and sheet["B2"].font.italic
        assert sheet["B2"].fill.fgColor.rgb == "FFCCEEDD"
        assert sheet.column_dimensions["B"].width == 41
        assert sheet.freeze_panes == "B2"
        highlight, = sheet.conditional_formatting["B2"]
        assert highlight.priority == 1 and highlight.dxf.fill.bgColor.rgb == "FFFFE7A7"
        existing, = sheet.conditional_formatting["B2:C2"]
        assert existing.priority == 2 and existing.dxf.fill.fgColor.rgb == "FFAACCFF"
        assert sheet["G2"].value == "=SUM(40,2)"
        assert sheet["G2"].number_format == "0.00"
        assert sheet["G3"].value == "first\rsecond"
        assert "A1:B1" in exported["Layout"].merged_cells
        with ZipFile(BytesIO(original)) as before, ZipFile(BytesIO(response.content)) as after:
            assert before.namelist() == after.namelist()
            assert {name for name in before.namelist() if before.read(name) != after.read(name)} == {
                "xl/styles.xml", "xl/worksheets/sheet1.xml",
            }
        assert client.get(f"/documents/{bom}/original").content == original

        ok(client.patch(f"/batches/{batch}/issues/{issue['id']}", json={"status": "false_positive"}))
        assert client.get(route).content == original
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "unknown"}))
        assert client.get(route).status_code == 409


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
        assert issue["evidence"][0]["scope"] == "page"
        result = ok(client.post(f"/documents/{quote}/search", json={"query": "8000"}))
        assert result["searchable"] and result["matches"][0]["source"] == 1

        import sqlite3
        with sqlite3.connect(tmp_path / "test.sqlite") as connection:
            connection.execute("UPDATE snapshots SET content = json_remove(content, '$.ocr_search') WHERE document = ?", (quote,))
        old_result = ok(client.post(f"/documents/{quote}/search", json={"query": "80"}))
        assert old_result["searchable"] and old_result["partial"] and old_result["matches"][0]["source"] == 1
        assert view["comparison"][0]["total"] is None


def test_unlabeled_roles_follow_contents_and_multiple_boms_block(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        ok(client.post(route, files={"file": ("quote.pdf", pdf([BOM]))}))
        ok(client.post(route, files={"file": ("BOM.xlsx", xlsx([OFFER], [*TERMS, "Supplier: Steel Co"]))}))
        view = ok(client.get(f"/batches/{batch}"))
        assert [(doc["role"], doc["name"]) for doc in view["documents"]] == [
            ("bom", "quote.pdf"), ("quote", "BOM.xlsx")]
        assert view["documents"][1]["supplier"] == "Steel Co"
        ok(client.post(route, files={"file": ("other.xlsx", xlsx([BOM]))}))
        blocked = client.post(f"/batches/{batch}/verify")
        assert blocked.status_code == 409 and "Multiple BOMs" in blocked.json()["detail"]


def test_review_actions_and_source_search(tmp_path):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        different = ["B-088", "Copper pipe", "2", "pcs", "100", "200"]
        bom = upload(client, batch, "BOM.pdf", pdf([BOM, [*different[:4], "", ""]], page=2,
                                                  terms=["Delivery: 2027-01-01"]))
        quote = upload(client, batch, "Offer.pdf", pdf([different], page=3), "Supplier A")
        view = ok(client.get(f"/batches/{batch}"))
        assert view["verification_action"] == {"kind": "scope", "document": bom}
        search = ok(client.post(f"/documents/{quote}/search", json={"query": "copper pipe"}))
        assert search["searchable"] and [match["source"] for match in search["matches"]] == [3]
        assert "Copper pipe" in search["matches"][0]["text"]
        thumbnail = client.get(f"/documents/{quote}/thumbnail/3")
        assert thumbnail.is_success and thumbnail.content.startswith(b"\x89PNG")
        assert len(thumbnail.content) < len(client.get(f"/documents/{quote}/page/3").content)
        assert client.get(f"/documents/{quote}/thumbnail/99").status_code == 404
        assert ok(client.post(f"/documents/{quote}/search", json={"query": "B-014"}))["matches"] == []
        unicode_note = "Straße " * 90 + "Copper marker"
        sheet = upload(client, batch, "Other.xlsx", xlsx([different], [f"Memo: {unicode_note}"]), "Supplier B")
        unicode_matches = ok(client.post(f"/documents/{sheet}/search", json={"query": "copper marker"}))["matches"]
        assert unicode_matches[0]["text"].endswith("Copper marker")
        matches = ok(client.post(f"/documents/{sheet}/search", json={"query": "B-088"}))["matches"]
        assert matches == [{"source": "Materials", "text": "B-088 | Copper pipe | 2 | pcs | 100 | 200"}]
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        view = ok(client.post(f"/batches/{batch}/verify"))
        missing = next(issue for issue in view["issues"] if issue["rule"] == "missing")
        assert any(e.get("scope") == "document" and e["document"] == quote for e in missing["evidence"])
        assert missing["search_hint"] == "B-014"
        freight = next(issue for issue in view["issues"] if issue["rule"] == "charges"
                       and issue["subject"][0] == quote)
        assert freight["source_editors"] == [{"document": quote, "target": "terms", "field": "freight"}]
        delivery = next(issue for issue in view["issues"] if issue["rule"] == "delivery")
        assert {editor["document"] for editor in delivery["source_editors"]} == {quote, sheet}
        assert all(editor["field"] == "delivery" for editor in delivery["source_editors"])
