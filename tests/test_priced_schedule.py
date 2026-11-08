"""Split material/labor table regression without supplier documents in the repository."""
from io import BytesIO

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.fixtures import xlsx
from tests.test_acceptance import create, ok


def priced_schedule():
    out = BytesIO()
    page = Canvas(out, pagesize=(842, 595))
    x_positions = [54, 210, 388, 426, 486, 558, 628, 705, 780]
    for vendor, site, service in (("Vendor One", "Site A", False), ("Vendor One", "Site A", True),
                                  ("Vendor Two", "Site B", False)):
        page.drawString(37, 552, vendor)
        page.drawRightString(801, 552, "RECONSTRUCTED / NOT ISSUED")
        page.drawCentredString(420, 522, "BILL OF MATERIALS & QUANTITIES")
        page.drawString(37, 491, "DOCUMENT REF: R-01")
        page.drawString(440, 491, f"PROJECT SITE: {site}")
        page.drawString(37, 466, "SCHEDULE B - NON-MATERIAL SERVICES" if service else "SCHEDULE A - MATERIAL QUANTITIES")
        for x, heading in zip(x_positions, ("NO.", "SPECIFICATION / WORK ITEM", "QTY", "UNIT", "MATERIAL",
                                            "LABOR", "MATERIAL", "LABOR", "LINE")):
            page.drawCentredString(x, 432, heading)
        for x, heading in zip(x_positions[4:], ("UNIT RATE", "UNIT RATE", "EXTENSION", "EXTENSION", "TOTAL")):
            page.drawCentredString(x, 417, heading)
        page.drawString(36, 395, "2 SCHEDULE SECTION")
        page.drawRightString(60, 370, "2" if service else "1")
        page.drawString(75, 370, "Site installation" if service else "Copper conductor 30 mm2")
        for x, value in zip(x_positions[2:], ("2", "lot" if service else "m", "-" if service else "125.00",
                                              "25.00", "-" if service else "250.00", "50.00",
                                              "50.00" if service else "300.00")):
            page.drawRightString(x + 12, 370, value)
        if service:
            page.drawString(462, 322, "TOTAL DETAILED SCHEDULE")
            page.drawRightString(798, 322, "PHP 350.00")
        page.showPage()
    page.save()
    return out.getvalue()


def test_split_schedule_keeps_prices_services_and_supplier_baselines_separate(tmp_path, monkeypatch):
    from service import gemini
    from service.adaptive import Interpretation

    monkeypatch.setenv("SURIQ_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")

    def interpret(model, schema, instructions, payload, **kwargs):
        vendor = next((element for element in payload["elements"] if element["text"].startswith("Vendor ")), None)
        if not vendor:
            return Interpretation(role="unknown", rationale="No change", evidence=[], rows=[], terms=[])
        name = vendor["text"].split(" RECONSTRUCTED")[0]
        return Interpretation.model_validate({
            "role": "quote", "rationale": "Supplier letterhead", "evidence": [vendor["id"]], "rows": [],
            "terms": [{"field": "supplier", "element": vendor["id"], "value": name}],
        })

    monkeypatch.setattr(gemini, "chat", interpret)
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        ok(client.post(route, files={"file": ("schedule.pdf", priced_schedule())}))
        ok(client.post(route, files={"file": ("offer.xlsx", xlsx(
            [["1", "Copper conductor 30 mm2", "2", "m", "125", "250"]], ["Supplier: Vendor One"]
        ))}))
        view = ok(client.get(f"/batches/{batch}"))
        bom, quote = view["documents"]
        material, service, other = bom["lines"]
        assert bom["status"] == "ready" and bom["role"] == "bom"
        assert not bom["classification"]["bundled_offers"]
        assert bom["provenance"]["provider"] == "gemini"
        assert material["fields"]["material"] == "Copper conductor 30 mm2"
        assert material["fields"]["unit_price"] == "125.00" and material["fields"]["amount"] == "250.00"
        assert material["fields"]["labor_amount"] == "50.00" and material["fields"]["line_total"] == "300.00"
        assert material["anchors"]["labor_amount"]["source"] == 1
        assert service["kind"] == "service" and service["schedule"] == material["schedule"]
        assert other["fields"]["code"] == material["fields"]["code"] and other["schedule"] != material["schedule"]
        assert bom["sources"][1]["schedule_totals"][0]["anchor"]["source"] == 2
        assert client.patch(f"/batches/{batch}/scope", json={"mode": "all"}).status_code == 422
        assert client.patch(f"/batches/{batch}/scope", json={"mode": "selected", "rows": [service["id"]]}).status_code == 422
        assert client.patch(f"/batches/{batch}/scope", json={
            "mode": "selected", "rows": [material["id"], other["id"]],
        }).status_code == 422
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "selected", "rows": [material["id"]]}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        match = result["matches"][f"{quote['id']}:{quote['lines'][0]['id']}"]
        assert match["bom"] == material["id"]
