"""Regression for centered PDF headers and non-itemized supplier quotations."""
from io import BytesIO

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.test_acceptance import create, ok


def make_pdf(draw):
    out = BytesIO()
    canvas = Canvas(out, pagesize=(595, 842))
    draw(canvas)
    canvas.showPage()
    canvas.save()
    return out.getvalue()


def test_right_aligned_bom_and_summary_quote_can_be_verified(tmp_path, monkeypatch):
    from service import gemini

    def draw_bom(page):
        page.drawString(36, 780, "BILL OF MATERIALS & QUANTITIES")
        page.drawString(36, 759, "Document reference: REQ-001")
        for x, name in zip((57, 207, 355, 391, 449, 517),
                           ("NO.", "ITEM SPECIFICATION", "QTY", "UNIT", "UNIT PRICE", "EXTENDED")):
            page.drawCentredString(x, 710, name)
        page.drawRightString(60, 680, "1")
        page.drawString(77, 680, "25 kVA distribution transformer, 7620/240 V")
        page.drawRightString(365, 680, "1")
        page.drawCentredString(391, 680, "unit")
        page.drawRightString(480, 680, "85,400.00")
        page.drawRightString(548, 680, "85,400.00")
        page.drawString(45, 635, "SUM OF 1 ITEM EXTENSIONS")
        page.drawRightString(548, 635, "PHP 85,400.00")
        page.drawString(45, 605, "MATERIALS TOTAL")
        page.drawRightString(548, 605, "PHP 85,400.00")

    def draw_quote(page):
        page.drawString(36, 795, "RECONSTRUCTED / NOT ISSUED")
        page.drawString(36, 780, "SUPPLIER QUOTATION")
        page.drawString(45, 710, "Materials (supplier-stated amount)")
        page.drawRightString(548, 710, "PHP 85,799.00")
        page.drawString(45, 685, "Labor, equipment, tools")
        page.drawRightString(548, 685, "PHP 5,000.00")
        page.drawString(45, 660, "TOTAL QUOTATION")
        page.drawRightString(548, 660, "PHP 90,799.00")
        page.drawString(45, 620, "Price validity:")
        page.drawString(280, 620, "20 days, subject to supplier confirmation")
        page.drawString(45, 594, "Quoted materials are detailed in document REQ-001.")

    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")

    def no_cloud(*args, **kwargs):
        raise AssertionError("A grounded schedule or summary should not invoke Gemini.")

    monkeypatch.setattr(gemini, "send", no_cloud)
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        for name, draw in (("schedule.pdf", draw_bom), ("offer.pdf", draw_quote)):
            ok(client.post(route, files={"file": (name, make_pdf(draw))}))
        view = ok(client.get(f"/batches/{batch}"))
        bom, quote = view["documents"]
        row, = bom["lines"]
        assert row["fields"]["code"] == "1"
        assert row["fields"]["material"] == "25 kVA distribution transformer, 7620/240 V"
        assert row["fields"]["unit_price"] == row["fields"]["amount"] == "85,400.00"
        assert bom["terms"]["item_extensions_total"] == "PHP 85,400.00"
        assert quote["role"] == "quote" and quote["summary_only"] and quote["lines"] == []
        assert quote["terms"]["materials_total"] == "PHP 85,799.00"
        assert quote["terms"]["total"] == "PHP 90,799.00"
        assert quote["terms"]["validity"] == "20 days, subject to supplier confirmation"
        assert quote["terms"]["draft_notice"] == "RECONSTRUCTED / NOT ISSUED"
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        assert not any(issue["rule"] == "missing" for issue in result["issues"])
        issue = next(issue for issue in result["issues"] if issue["rule"] == "materials_summary")
        assert "399.00" in issue["note"] and issue["confirmable"]
        assert {anchor["document"] for anchor in issue["evidence"] if anchor["boxes"]} == {bom["id"], quote["id"]}
        comparison, = result["comparison"]
        assert comparison["total"] is None
        assert comparison["items"][row["id"]]["state"] == "Not itemized"
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "selected", "rows": [row["id"]]}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        assert not any(issue["rule"] == "materials_summary" for issue in result["issues"])
