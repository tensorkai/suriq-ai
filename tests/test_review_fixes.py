from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import Workbook

from service.api import create_app
from tests.test_acceptance import create, ok, upload


def sheet(headers, rows, terms=()):
    workbook = Workbook()
    page = workbook.active
    page.append(headers)
    for row in rows:
        page.append(row)
    for term in terms:
        page.append(term.split(": ", 1))
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


HEADERS = ["Item ID", "Material", "Qty", "Unit", "Unit price", "Amount"]
TERMS = ["Tax: VAT inclusive", "Freight: Included"]


def verify(tmp_path, bom, quote):
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", bom)
        upload(client, batch, "Quote.xlsx", quote, "Supplier A")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        return ok(client.post(f"/batches/{batch}/verify"))


def test_row_numbers_do_not_pair_different_materials(tmp_path):
    bom = sheet(HEADERS, [["1", "Portland cement", "100", "bags", "", ""],
                          ["2", "Deformed steel bar", "50", "pcs", "", ""]])
    quote = sheet(HEADERS, [["1", "Deformed steel bar", "50", "pcs", "300", "15000"],
                            ["2", "Portland cement", "100", "bags", "250", "25000"]], TERMS)
    data = verify(tmp_path, bom, quote)
    bom_doc, quote_doc = data["documents"]
    names = {line["id"]: line["fields"]["material"] for line in bom_doc["lines"]}
    for line in quote_doc["lines"]:
        match = data["matches"][f"{quote_doc['id']}:{line['id']}"]
        assert names[match["bom"]] == line["fields"]["material"]
    assert not [issue for issue in data["issues"] if issue["rule"] in {"material", "quantity"}]


def test_different_bag_size_with_same_total_is_one_pack_finding(tmp_path):
    headers = ["Item ID", "Material", "Qty", "Unit", "Pack", "Unit price", "Amount"]
    bom = sheet(headers, [["C-01", "Portland cement", "100", "bags", "40 kg/bag", "", ""]])
    quote = sheet(headers, [["C-01", "Portland cement", "80", "bags", "50 kg/bag", "300", "24000"]], TERMS)
    issues = verify(tmp_path, bom, quote)["issues"]
    assert not [issue for issue in issues if issue["rule"] == "quantity"]
    pack = next(issue for issue in issues if issue["rule"] == "pack")
    assert "Same total of 4000 kg" in pack["note"]

    short = sheet(headers, [["C-01", "Portland cement", "70", "bags", "50 kg/bag", "300", "21000"]], TERMS)
    quantity = next(issue for issue in verify(tmp_path / "short", bom, short)["issues"] if issue["rule"] == "quantity")
    assert quantity["condition"] == ["4000kg", "3500kg"]


def test_specifications_column_and_blank_quotation_form(tmp_path):
    from tests.fixtures import docx

    headers = ["Item No.", "Specifications", "Qty", "Unit", "Unit Price", "Amount"]
    rows = [headers, ["1", "Portland cement, Type 1, 40 kg/bag", "100", "bags", "", ""],
            ["2", "Deformed steel bar 12 mm x 6 m, Grade 40", "50", "pcs", "", ""]]
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        rfq = ok(client.post(route, files={"file": ("rfq.docx", docx(["Request for Quotation"], rows))}))["id"]
        blank = ok(client.post(route, files={"file": ("form.docx", docx(["Quotation"], rows))}))["id"]
        docs = {doc["id"]: doc for doc in ok(client.get(f"/batches/{batch}"))["documents"]}
    assert [line["fields"]["description"] for line in docs[rfq]["lines"]] == [
        "Portland cement, Type 1, 40 kg/bag", "Deformed steel bar 12 mm x 6 m, Grade 40"]
    assert docs[rfq]["role"] == "buyer"
    assert docs[blank]["role"] != "quote" and docs[blank]["status"] == "needs_review"
    assert "blank response form" in docs[blank]["classification"]["basis"]


def test_bom_rows_are_checked_before_comparison(tmp_path):
    bom = sheet(HEADERS, [["C-01", "Portland cement", "100", "bags", "", ""],
                          ["C-01", "Deformed steel bar", "50", "pcs", "", ""],
                          ["C-03", "Tie wire", "0", "kg", "", ""],
                          ["C-04", "Plywood", "10", "shts", "", ""]])
    quote = sheet(HEADERS, [["C-03", "Tie wire", "5", "kg", "90", "450"]], TERMS)
    issues = [issue for issue in verify(tmp_path, bom, quote)["issues"] if issue["rule"] == "baseline"]
    assert sorted(issue["condition"] for issue in issues) == ["duplicate item ID", "quantity", "unit"]
    assert all(issue["title"] == "BOM line needs clarification" for issue in issues)


def test_suggested_match_can_be_accepted_and_reverified(tmp_path):
    bom = sheet(HEADERS, [["C-01", "Portland cement", "100", "bags", "", ""],
                          ["C-02", "Tie wire", "5", "kg", "", ""]])
    quote = sheet(HEADERS, [["Q-9", "Cement Portland Type 1", "100", "bags", "250", "25000"]], TERMS)
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "BOM.xlsx", bom)
        offer = upload(client, batch, "Quote.xlsx", quote, "Supplier A")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        issue = next(issue for issue in data["issues"] if issue["rule"] == "match")
        quote_id, line = issue["subject"]
        candidate = data["matches"][f"{quote_id}:{line}"]["candidates"][0]
        data = ok(client.patch(f"/documents/{offer}/line/{line}", json={
            "values": {}, "anchors": {}, "match": candidate, "note": "Accepted suggested BOM match."}))
    assert not [issue for issue in data["issues"] if issue["rule"] == "match"]
    assert data["matches"][f"{quote_id}:{line}"] == {"bom": candidate, "candidates": [candidate], "method": "manual"}


def test_native_title_over_scanned_table_is_read_with_ocr(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    from tests.fixtures import COLUMNS

    scan = Image.new("RGB", (1240, 600), "white")
    draw = ImageDraw.Draw(scan)
    font = ImageFont.truetype("DejaVuSans.ttf", 26)
    for offset, row in enumerate([HEADERS, ["B-014", "Deformed bar 16 mm", "80", "pcs", "", ""]]):
        for x, value in zip(COLUMNS, row):
            draw.text((x * 2, 40 + offset * 60), value, fill="black", font=font)
    output = BytesIO()
    page = Canvas(output)
    page.drawString(40, 800, "BILL OF MATERIALS")
    page.drawImage(ImageReader(scan), 0, 450, width=595, height=288)
    page.showPage()
    page.save()
    with TestClient(create_app(tmp_path / "test.sqlite")) as client:
        batch = create(client)
        key = upload(client, batch, "scan.pdf", output.getvalue())
        doc = next(doc for doc in ok(client.get(f"/batches/{batch}"))["documents"] if doc["id"] == key)
    assert doc["sources"][0]["method"] == "ocr"
    assert [line["fields"]["material"] for line in doc["lines"]] == ["Deformed bar 16 mm"]
