"""R1 and BOQ regressions through upload, scope, and verification."""
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.fixtures import BOM, xlsx
from tests.test_acceptance import create, ok


def materials_pair(*, allowance=True, subtotal=True, quoted="387,337.68", currency="PHP",
                   reference="REQ-007", selected=False, estimate=False, scope_conflict=False):
    def page(lines, out=None, *, rows=False):
        out = out or BytesIO()
        pdf = Canvas(out)
        for i, text in enumerate(lines):
            pdf.drawString(40, 795 - i * 23, text)
        if rows:
            for x, heading, value in zip(
                (40, 104, 350, 396, 440, 520),
                ("Item ID", "Material", "Qty", "Unit", "Unit price", "Amount"),
                ("01", "Copper conductor 30 mm2", "12", "m", "32244.89", "386938.68"),
            ):
                pdf.drawString(x, 700, heading)
                pdf.drawString(x, 676, value)
        return pdf, out

    pdf, out = page(["BILL OF QUANTITIES", "Document reference: REQ-007", "Project Site: Sual"], rows=True)
    pdf.showPage()
    lines = ["BILL OF QUANTITIES"]
    if subtotal:
        lines.append("SUM OF 36 ITEM EXTENSIONS        PHP 386,938.68")
    if allowance:
        lines.append("RECONCILIATION ALLOWANCE        PHP 399.00")
    label = "ESTIMATED MATERIALS TOTAL" if estimate else "MATERIAL COST PER PROJECT QUOTATION"
    lines.append(f"{label}        PHP 387,337.68")
    if selected:
        lines.extend(("Materials scope: Phase A", "Selected scope materials total: PHP 387,337.68"))
    for i, line in enumerate(lines):
        pdf.drawString(40, 795 - i * 23, line)
    pdf.showPage()
    pdf.save()
    supplier, result = page([
        "SUPPLIER QUOTATION", "Supplier: Fixture Supplier", "Project Site: Sual",
        f"Materials cost: {currency} {quoted}", f"Total quotation: {currency} 513,538.98",
        "Mobilization and consumables: PHP 10,000.00", "VAT (as quoted): PHP 0.00",
        f"Quoted materials are detailed in document {reference}. Price validity: 20 calendar days from quotation date.",
        "Quotation date: 08 November 2026",
        *(["Materials scope: Phase A", f"Selected scope materials total: {currency} {quoted}"] if selected else []),
        *(["Materials scope: Phase A", "Materials scope: Phase B"] if scope_conflict else []),
    ])
    supplier.showPage()
    supplier.save()
    return out.getvalue(), result.getvalue()


@pytest.mark.parametrize(("options", "mode", "status", "arithmetic", "finding"), [
    ({}, "all", "Matches", "Verified", None),
    ({"quoted": "387,337.69"}, "all", "Differs", "Verified", "materials_summary"),
    ({"allowance": False}, "all", "Unresolved", "Unresolved", "materials_reconciliation"),
    ({"subtotal": False, "allowance": False}, "all", "Matches", "Unverified", None),
    ({"currency": "USD"}, "all", "Unresolved", "Unresolved", None),
    ({"reference": "OTHER-001"}, "all", "Unresolved", "Unresolved", None),
    ({"scope_conflict": True, "quoted": "387,337.69"}, "all", "Unresolved", "Unresolved", None),
    ({"quoted": "387,337.69"}, "selected", "Unresolved", "Unresolved", None),
    ({"selected": True}, "selected", "Matches", "Unverified", None),
    ({"estimate": True, "quoted": "387,337.69"}, "all", "Price variance", "Verified", "materials_price_variance"),
])
def test_materials_reconcile_only_on_documented_basis(tmp_path, monkeypatch, options, mode, status, arithmetic, finding):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    with TestClient(create_app(tmp_path / "review.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        for name, blob in zip(("requirements.pdf", "quotation.pdf"), materials_pair(**options)):
            ok(client.post(route, files={"file": (name, blob)}))
        view = ok(client.get(f"/batches/{batch}"))
        baseline = next(doc for doc in view["documents"] if doc["role"] == "boq")
        rows = [baseline["lines"][0]["id"]] if mode == "selected" else []
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": mode, "rows": rows}))
        verified = ok(client.post(f"/batches/{batch}/verify"))
        comparison, = verified["comparison"]
        materials = comparison["materials"]
        assert (materials["status"], materials["arithmetic"]) == (status, arithmetic)
        issues = [item for item in verified["issues"] if item["rule"].startswith("materials_")]
        assert [item["rule"] for item in issues] == ([finding] if finding else [])
        assert comparison["total"] is None
        if not options:
            assert materials["subtotal"] == "386938.68"
            assert materials["adjustments"][0]["value"] == "399.00"
            assert materials["expected"] == materials["offered"] == "387337.68"
            assert {(anchor["document"], anchor["source"]) for anchor in materials["evidence"]} >= {
                (baseline["id"], 2), (comparison["document"], 1),
            }
        if finding == "materials_summary":
            assert materials["difference"] == "0.01"


def test_select_between_bom_and_boq_and_use_buyer_requirements(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    with TestClient(create_app(tmp_path / "review.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        for name, blob in zip(("requirements.pdf", "quotation.pdf"), materials_pair()):
            ok(client.post(route, files={"file": (name, blob)}))
        bom = ok(client.post(route, files={"file": ("bom.xlsx", xlsx([BOM]))}))["id"]
        buyer = ok(client.post(route, files={"file": ("rfq.xlsx", xlsx([], [
            "Request for quotation: RFQ-2", "Delivery: 10 calendar days from PO",
        ]))}))["id"]
        pending = ok(client.get(f"/batches/{batch}"))
        assert next(doc for doc in pending["documents"] if doc["id"] == buyer)["role"] == "buyer"
        assert client.post(f"/batches/{batch}/verify").status_code == 409
        selected = ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all", "baseline": bom}))
        assert selected["baseline_document"] == bom
        assert ok(client.post(f"/batches/{batch}/verify"))["baseline_document"] == bom


def test_boq_offer_format_and_bundled_work_remain_distinct(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    materials = ["A-01", "Ready-mix concrete", "20", "m3", "", ""]
    installed = ["A-02", "Supply and install site lighting", "4", "units", "5000", "20000"]
    with TestClient(create_app(tmp_path / "review.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        for name, blob in (
            ("baseline.xlsx", xlsx([materials, installed], ["Bill of Quantities: Scope of work"])),
            ("offer.xlsx", xlsx([[*materials[:4], "5000", "100000"]], [
                "Bill of Quantities: Supplier schedule", "Supplier quotation: Bid", "Supplier: Supply Co",
                "Quote reference: SQ-2", "Tax: VAT inclusive", "Freight: Included",
            ])),
        ):
            ok(client.post(route, files={"file": (name, blob)}))
        view = ok(client.get(f"/batches/{batch}"))
        baseline = next(doc for doc in view["documents"] if doc["role"] == "boq")
        quote = next(doc for doc in view["documents"] if doc["role"] == "quote")
        assert quote["classification"]["document_type"] == "boq"
        assert baseline["lines"][1]["kind"] == "work"
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        assert any(issue["rule"] == "work_scope" and issue["evidence"][0]["cells"] for issue in result["issues"])
        assert not any(issue["rule"] == "missing" for issue in result["issues"])
        assert list(result["comparison"][0]["items"]) == [baseline["lines"][0]["id"]]


def test_baseline_changes_keep_manual_matches_and_factors_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    with TestClient(create_app(tmp_path / "review.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        rows = [["A-01", "Copper cable", 2, "m", "", ""], ["A-02", "Conduit", 2, "m", "", ""]]
        bom = ok(client.post(route, files={"file": ("bom.xlsx", xlsx(rows))}))["id"]
        boq = ok(client.post(route, files={"file": ("boq.xlsx", xlsx(rows, ["Bill of Quantities: Required items"]))}))["id"]
        quote = ok(client.post(route, files={"file": ("quote.xlsx", xlsx(
            [["A-01", "Copper cable", 2, "m", 10, 20]], ["Supplier: Fixture Vendor"],
        ))}))["id"]
        scope = f"/batches/{batch}/scope"
        correction = f"/documents/{quote}/line/Materials:2"
        ok(client.patch(scope, json={"mode": "all", "baseline": bom}))
        ok(client.patch(correction, json={"match": "Materials:3", "note": "Reviewed BOM pairing"}))
        ok(client.patch(scope, json={"mode": "all", "baseline": boq}))
        view = ok(client.patch(correction, json={"factor": "2", "note": "BOQ conversion"}))
        line = next(doc for doc in view["documents"] if doc["id"] == quote)["lines"][0]
        assert line.get("match") is None and line["factor"] == "2"
        view = ok(client.patch(scope, json={"mode": "all", "baseline": bom}))
        line = next(doc for doc in view["documents"] if doc["id"] == quote)["lines"][0]
        assert line["match"] == "Materials:3" and "factor" not in line
