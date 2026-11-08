import csv
import re
from io import StringIO
from subprocess import run

import pytest
from fastapi.testclient import TestClient

from service.api import create_app
from tests.fixtures import xlsx
from tests.test_acceptance import create, ok, upload
from tests.test_revision_materials import materials_pair


@pytest.mark.parametrize("offer_count", [1, 5])
def test_summary_and_structured_pdf_share_verified_values(tmp_path, offer_count):
    rows = [[f"B-{index:03}", "Deformed bar 16 mm Grade 60", "2", "pcs", "", ""]
            for index in range(28)]
    note = "Confirm the drawing revision.\nAsk whether the replacement is in stock."
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}"
        upload(client, batch, "Requirements.xlsx", xlsx(rows))
        for index in range(offer_count):
            material = "Deformed bar 12 mm Grade 60" if index == 0 else rows[0][1]
            quote_rows = [[row[0], material, *row[2:4], str(100 + index), str(2 * (100 + index))]
                          for row in rows]
            upload(client, batch, f"Offer-{index + 1}.xlsx", xlsx(quote_rows, [
                f"Reference: QUOTE-{index + 1}", "Tax: VAT inclusive", "Freight: Included",
                "Charges: None", "Discount: None", f"Total: {28 * 2 * (100 + index)}",
            ]), f"Supplier {index + 1} with a long name for the column wrapping check",
                coverage="partial" if index == 4 else "full")
        assert client.get(f"{route}/export/pdf").status_code == 409
        ok(client.patch(f"{route}/scope", json={"mode": "all"}))
        verified = ok(client.post(f"{route}/verify"))
        issue = next(item for item in verified["issues"] if item["rule"] == "dimension")
        assert issue["detail"]["expected"] == "16 mm"
        assert "12 mm" in issue["detail"]["observed"]
        assert "Supplier 1" in issue["detail"]["context"]
        ok(client.patch(f"{route}/issues/{issue['id']}", json={
            "status": "false_positive", "note": note, "fingerprint": issue["fingerprint"],
        }))
        loaded = ok(client.get(route))
        assert next(item for item in loaded["issues"] if item["id"] == issue["id"])["review"]["note"] == note
        summary = loaded["summary"]
        assert summary["scope"] == "All 28 material rows"
        assert len(summary["offers"]) == offer_count
        assert [offer["comparable"] for offer in summary["offers"]] == (
            [False] if offer_count == 1 else [True, True, True, False, False])
        first = next(offer for offer in summary["offers"] if offer["cells"]["reference"]["value"] == "QUOTE-1")
        assert first["cells"]["quoted_total"]["value"] == "₱5,600"
        assert first["cells"]["payable"]["value"] == "Incomparable"
        assert first["cells"]["freight"]["evidence"]
        assert issue["id"] not in first["cells"]["findings"]["findings"]

        exported = client.get(f"{route}/export/pdf")
        assert exported.is_success
        (tmp_path / "verification.pdf").write_bytes(exported.content)
        extracted = run(["pdftotext", "-layout", "-", "-"], input=exported.content,
                        capture_output=True, check=True).stdout.decode()
        pages = extracted.split("\f")
        assert "Supplier Quotation Summary" in pages[0]
        assert "Internal canvass" in pages[0]
        assert "QUOTE-1" in pages[0] and "5,600" in pages[0]
        for section in ("Scope and sources", "Findings", "Detailed line items", "Page 2"):
            assert section in extracted
        assert re.search(r"Confirm the drawing revision\.\s*\n\s*Ask whether the replacement is in stock\.", extracted)
        assert "Materials B2" in extracted
        machine_readable = client.get(f"{route}/export/csv").content.decode("utf-8-sig")
        assert any(row[0] == "Issue" and row[-1] == note for row in csv.reader(StringIO(machine_readable)))


def test_summary_only_quote_displays_verified_basis_and_unresolved_costs(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}"
        for name, contents in zip(("Requirements.pdf", "Offer.pdf"), materials_pair()):
            ok(client.post(f"{route}/documents", files={"file": (name, contents)}))
        ok(client.patch(f"{route}/scope", json={"mode": "all"}))
        verified = ok(client.post(f"{route}/verify"))
        assert verified["summary"]["site"] == "Sual"
        offer, = verified["summary"]["offers"]
        cells = offer["cells"]
        assert "₱386,938.68 + ₱399.00 = ₱387,337.68" in cells["materials"]["value"]
        assert {anchor["source"] for anchor in cells["materials"]["evidence"]} == {1, 2}
        assert cells["quoted_materials"]["value"] == "₱387,337.68"
        assert cells["quoted_total"]["value"] == "₱513,538.98"
        assert cells["payable"]["value"] == "Incomparable"
        assert cells["coverage"]["value"].startswith("Summary only.")
        assert cells["tax"]["reasons"] == [
            "VAT basis unclear. The quote lists ₱0.00 VAT but does not state how VAT applies to the prices."
        ]
        assert cells["freight"]["reasons"] == ["Freight unstated. Confirm whether delivery is included or separately charged."]
        assert "10,000.00" in cells["mobilization"]["value"]
        assert "2026-11-28" in cells["validity"]["value"]
        exported = client.get(f"{route}/export/pdf")
        assert exported.is_success
        (tmp_path / "verification.pdf").write_bytes(exported.content)
        extracted = run(["pdftotext", "-layout", "-", "-"], input=exported.content,
                        capture_output=True, check=True).stdout.decode()
        assert "PHP 386,938.68 + PHP 399.00 = PHP 387,337.68" in extracted
        assert "PHP 513,538.98" in extracted and "Expiry · 2026-11-28" in extracted
        assert "Offer.pdf · p. 1" in extracted and "Requirements.pdf · p. 2" in extracted
        assert extracted.count("Summary only. Line coverage") == 1
        assert "Not itemized" not in extracted
