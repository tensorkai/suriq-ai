"""Hosted provider contract and source grounding."""
import json
from io import BytesIO
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service import gemini
from service.api import create_app
from tests.test_acceptance import create, ok


def test_gemini_reads_uploads_and_retries_without_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    requests = []

    def send(request, timeout):
        requests.append(request)
        assert request.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
        assert request.get_header("X-goog-api-key") == "synthetic-test-key"
        body = json.loads(request.data)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["temperature"] == 0.2
        assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"
        parts = body["contents"][0]["parts"]
        assert parts[1]["inlineData"]["mimeType"] == "image/png"
        elements = json.loads(parts[0]["text"])["elements"]
        row = next(element["id"] for element in elements if "Anchor bolts" in element["text"])
        heading = next(element["id"] for element in elements if "Quotation" in element["text"])
        response = {"role": "quote", "rationale": "Offer heading", "evidence": [heading],
                    "rows": [{"origin": "unknown-cell", "fields": [
                        {"field": "material", "element": "unknown-cell", "value": "Imaginary cable"},
                    ]}, {"origin": row, "fields": [
                        {"field": "material", "element": row, "value": "Anchor bolts"},
                        {"field": "quantity", "element": row, "value": "50"},
                        {"field": "unit", "element": row, "value": "pcs"},
                        {"field": "unit_price", "element": row, "value": "12.50"},
                    ]}], "terms": []}
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(response)}]}}]}

    monkeypatch.setattr(gemini, "send", send)
    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(40, 790, "Quotation")
    canvas.drawString(40, 740, "Quantity booked 50 | Anchor bolts | measure pcs | each 12.50")
    canvas.showPage()
    canvas.save()
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        key = ok(client.post(route, files={"file": ("unfamiliar.pdf", output.getvalue())}))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        assert view["ai"] == {"provider": "gemini", "configured": True}
        assert len(requests) == 1 and view["documents"][0]["role"] == "quote"
        ok(client.post(f"/documents/{key}/retry"))
        view = ok(client.get(f"/batches/{batch}"))
        doc, = view["documents"]
        assert len(requests) == 2 and doc["role"] == "quote"
        assert doc["provenance"]["provider"] == "gemini"
        assert doc["provenance"]["discarded_citations"] == 1 and len(doc["lines"]) == 1
        quantity = doc["lines"][0]["anchors"]["quantity"]
        assert quantity["text"] == "50" and quantity["boxes"] and quantity["weak"]
        assert "synthetic-test-key" not in json.dumps(view)


def test_gemini_quota_error_is_actionable_and_has_no_retry(monkeypatch):
    import pytest
    from service.ollama import InferenceError
    from service.suggestions import Candidates

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    count = 0

    def quota(request, timeout):
        nonlocal count
        count += 1
        raise HTTPError(request.full_url, 429, "Rate limited", {}, None)

    monkeypatch.setattr(gemini, "send", quota)
    with pytest.raises(InferenceError, match="quota"):
        gemini.chat(gemini.model_name(), Candidates, "Select IDs.", {}, retries=1)
    assert count == 1


def test_gemini_omits_remote_bounds_but_enforces_them_locally(monkeypatch):
    import pytest
    from service.adaptive import Interpretation
    from service.ollama import InferenceError

    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")

    def send(request, timeout):
        schema = json.loads(request.data)["generationConfig"]["responseJsonSchema"]
        assert "$defs" in schema
        assert "maxItems" not in json.dumps(schema) and "maxLength" not in json.dumps(schema)
        output = {"role": "unknown", "rationale": "x" * 121, "evidence": [], "rows": [], "terms": []}
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(output)}]}}]}

    monkeypatch.setattr(gemini, "send", send)
    with pytest.raises(InferenceError, match="invalid or incomplete structured output"):
        gemini.chat(gemini.model_name(), Interpretation, "Interpret this source.", {})


def test_quotation_bundle_keeps_each_supplier_pdf_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "off")
    output = BytesIO()
    page = Canvas(output)
    for supplier, total in (("Northside Electric", "1,000.00"), ("Southside Electric", "2,000.00")):
        page.drawString(40, 790, supplier)
        page.drawRightString(540, 790, "QUOTATION")
        page.drawString(40, 740, "TOTAL CONTRACT PRICE")
        page.drawRightString(540, 740, f"PHP {total}")
        page.showPage()
    page.save()
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        key = ok(client.post(f"/batches/{batch}/documents", files={"file": ("bundle.pdf", output.getvalue())}))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        doc = next(item for item in view["documents"] if item["id"] == key)
        assert doc["role"] == "bundle"
        assert doc["bundle"]["offers"] == [
            {"supplier": "Northside Electric", "reference": "", "pages": [1]},
            {"supplier": "Southside Electric", "reference": "", "pages": [2]},
        ]
        assert client.patch(f"/documents/{key}/role", data={
            "role": "quote", "supplier": "Northside Electric", "reason": "Attempt to merge offers",
        }).status_code == 422
        offer = client.get(f"/documents/{key}/offer/2")
        assert offer.is_success and offer.content.startswith(b"%PDF")
        quote = next(item for item in view["documents"] if item["bundle_parent"] == key and item["bundle_pages"] == [2])
        assert quote["supplier"] == "Southside Electric" and quote["summary_only"]
        assert quote["terms"]["total"] == "PHP 2,000.00"
