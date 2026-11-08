from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.fixtures import BOM, TERMS, xlsx
from tests.test_acceptance import create, ok, upload


def summary_quote():
    output = BytesIO()
    page = Canvas(output)
    page.setFont("Helvetica", 10)
    for text, y in (
        ("SUPPLIER QUOTATION", 790), ("Date: 08 November 2026", 765),
        ("Materials PHP 387,337.68", 730), ("VAT (as quoted) PHP 0.00", 710),
        ("Mobilization and Consumables PHP 10,000.00", 690),
        ("TOTAL PROJECT COST PHP 397,337.68", 670),
        ("3. PAYMENT TERMS", 630), ("40% upon acceptance of the quotation", 610),
        ("60% upon completion of the works", 590),
        ("4. MATERIALS PROCUREMENT AND LEAD TIME", 550),
        ("Material delivery lead time: approximately 15 days from payment acceptance, subject to an agreed work", 530),
        ("schedule.", 515),
        ("Quoted materials are detailed in document DEMO-BOM-007. Price validity: 20 calendar days from quotation date.", 495),
        ("7. WARRANTY", 460), ("A one-year service and materials warranty against defects.", 440),
        ("8. EXCLUSIONS", 400), ("Authority permits and unforeseen civil works are excluded.", 380),
    ):
        page.drawString(36, y, text)
    page.save()
    return output.getvalue()


def test_summary_offer_preserves_northline_timing_terms_and_cost_explanations(tmp_path):
    with TestClient(create_app(tmp_path / "terms.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "requirements.xlsx", xlsx([BOM]))
        offer = upload(client, batch, "summary.pdf", summary_quote(), "Demonstration Supplier")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        comparison, = result["comparison"]
        commercial = comparison["commercial"]
        terms = {row["field"]: row for row in commercial["terms"]}
        assert {terms[field]["status"] for field in ("payment", "delivery", "warranty", "exclusions")} == {"Supplier stated"}
        assert "40%" in terms["payment"]["offered"] and "60%" in terms["payment"]["offered"]
        assert terms["delivery"]["offered"].endswith("schedule.")
        assert all(anchor["document"] == offer and anchor["boxes"] for row in terms.values() for anchor in row["evidence"])
        validity = commercial["validity"]
        assert validity["expiry"] == "2026-11-28"
        assert validity["issue_date"] == "2026-11-08"
        assert validity["duration"]["trigger"] == "quotation date"
        assert len(validity["evidence"]) == 2
        costs = {row["field"]: row for row in commercial["costs"]}
        assert costs["vat"]["reason"] == "VAT basis unclear. The quote lists ₱0.00 VAT but does not state how VAT applies to the prices."
        assert costs["freight"]["reason"] == "Freight unstated. Confirm whether delivery is included or separately charged."
        assert costs["mobilization"]["value"] == "PHP 10,000.00"
        assert comparison["total"] is None


def test_buyer_delivery_compares_only_the_same_trigger(tmp_path):
    with TestClient(create_app(tmp_path / "terms.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "requirements.xlsx", xlsx([BOM]))
        rfq = ok(client.post(f"/batches/{batch}/documents", files={
            "file": ("rfq.xlsx", xlsx([], ["Delivery: 10 calendar days from PO"])),
        }, data={"role": "buyer"}))["id"]
        same = upload(client, batch, "same.xlsx", xlsx([[*BOM[:4], 100, 8000]], [
            *TERMS, "Delivery: 15 calendar days from purchase order",
        ]), "Same trigger")
        different = upload(client, batch, "different.xlsx", xlsx([[*BOM[:4], 100, 8000]], [
            *TERMS, "Delivery: 15 calendar days from payment acceptance",
        ]), "Different trigger")
        conflict = upload(client, batch, "conflict.xlsx", xlsx([[*BOM[:4], 100, 8000]], [
            *TERMS, "Delivery: 10 calendar days from PO", "Delivery: 15 calendar days from PO",
        ]), "Conflicting dates")
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        deliveries = {row["document"]: next(term for term in row["commercial"]["terms"]
                      if term["field"] == "delivery") for row in result["comparison"]}
        assert deliveries[same]["status"] == "Differs"
        assert deliveries[different]["status"] == "Needs clarification"
        assert deliveries[conflict]["status"] == "Needs clarification"
        assert "10 calendar days" in deliveries[conflict]["offered"] and "15 calendar days" in deliveries[conflict]["offered"]
        assert {anchor["document"] for anchor in deliveries[same]["evidence"]} == {rfq, same}
        assert "purchase order" in deliveries[different]["reason"] and "payment acceptance" in deliveries[different]["reason"]


def test_validity_uses_manila_date_and_cutoff():
    from service.commercial import validity_interpretation

    manila = ZoneInfo("Asia/Manila")
    terms = {"quote_date": "8 Nov 2026", "validity": "20 calendar days from quotation date"}
    assert validity_interpretation(terms, datetime(2026, 11, 28, 23, 59, tzinfo=manila))["status"] == "Valid"
    assert validity_interpretation(terms, datetime(2026, 11, 29, 0, 0, tzinfo=manila))["status"] == "Expired"
    terms["validity"] = "until 28 November 2026 at 5 p.m."
    assert validity_interpretation(terms, datetime(2026, 11, 28, 17, 0, tzinfo=manila))["expiry"] == "2026-11-28T17:00:00+08:00"
    assert validity_interpretation(terms, datetime(2026, 11, 28, 17, 1, tzinfo=manila))["status"] == "Expired"
    terms["validity"] = "20 days from quotation date"
    assert "calendar or business" in validity_interpretation(terms, datetime(2026, 11, 28, tzinfo=manila))["reason"]


def test_equal_money_mentions_keep_locations_and_distinct_values_raise_findings(tmp_path):
    with TestClient(create_app(tmp_path / "terms.sqlite")) as client:
        batch = create(client)
        upload(client, batch, "requirements.xlsx", xlsx([BOM]))
        ids = {}
        for label, totals in (
            ("Same meaning", ["VAT: PHP 0.00", "VAT: 0", "Total: PHP 8,000.00", "Total: 8000"]),
            ("Different value", ["Total: PHP 8,000.00", "Total: 8000.01"]),
            ("Different currency", ["Total: PHP 8,000.00", "Total: USD 8000"]),
        ):
            ids[label] = upload(client, batch, f"{label}.xlsx", xlsx([[*BOM[:4], 100, 8000]], [
                *TERMS, "Currency: PHP", *totals,
            ]), label)
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        result = ok(client.post(f"/batches/{batch}/verify"))
        assert {tuple(issue["subject"]) for issue in result["issues"] if issue["rule"] == "terms"} == {
            (ids["Different value"], "total"), (ids["Different currency"], "total"),
        }
        same = next(doc for doc in result["documents"] if doc["id"] == ids["Same meaning"])
        assert len(same["term_mentions"]["total"]) == len(same["term_mentions"]["vat"]) == 2
        assert len({entry["anchor"]["cells"][0] for entry in same["term_mentions"]["total"]}) == 2
        assert next(row for row in result["comparison"] if row["document"] == same["id"])["total"] == "8000.00"


def test_model_term_citations_require_the_matching_label():
    import pytest

    from service.adaptive import Interpretation, reconcile
    from service.ollama import InferenceError

    elements = {
        "e0": (1, {"source": 1, "text": "Tools, Equipment & Labor PHP 116,201.30",
                    "weak": False, "cells": ["A1"]}, None),
        "e1": (2, {"source": 1, "text": "Price validity: 20 calendar days from quotation date.",
                    "weak": False, "cells": ["A2"]}, None),
    }
    reading = Interpretation.model_validate({
        "role": "unknown", "rationale": "Source terms", "evidence": [], "rows": [],
        "terms": [{"element": "e1", "value": "20 calendar days", "field": "validity"}],
    })
    _, terms = reconcile(reading, elements, 1)
    field, value, anchor = terms[0]
    assert field == "validity" and value == "20 calendar days from quotation date."
    assert anchor["method"] == "model" and not anchor["weak"]

    with pytest.raises(InferenceError, match="different labeled field"):
        reconcile(Interpretation.model_validate({
            "role": "unknown", "rationale": "Source terms", "evidence": [], "rows": [],
            "terms": [{"element": "e0", "value": "116,201.30", "field": "materials_total"}],
        }), elements, 1)
