"""Hosted provider contract and the per-action sharing boundary."""
import json
from io import BytesIO
from urllib.error import HTTPError

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service import gemini
from service.api import create_app
from tests.test_acceptance import create, ok


def test_gemini_requires_upload_opt_in_and_preserves_pdf_anchors(tmp_path, monkeypatch):
    monkeypatch.setenv("SURIQ_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    requests = []

    def send(request, timeout):
        requests.append(request)
        assert request.full_url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
        assert request.get_header("X-goog-api-key") == "synthetic-test-key"
        body = json.loads(request.data)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"
        parts = body["contents"][0]["parts"]
        assert parts[1]["inlineData"]["mimeType"] == "image/png"
        elements = json.loads(parts[0]["text"])["elements"]
        row = next(element["id"] for element in elements if "Anchor bolts" in element["text"])
        heading = next(element["id"] for element in elements if "Quotation" in element["text"])
        response = {"role": "quote", "rationale": "Offer heading", "evidence": [heading],
                    "rows": [{"origin": row, "fields": [
                        {"field": "material", "element": row, "value": "Anchor bolts"},
                        {"field": "quantity", "element": row, "value": "50"},
                        {"field": "unit", "element": row, "value": "pcs"},
                    ]}], "terms": []}
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(response)}]}}]}

    monkeypatch.setattr(gemini, "send", send)
    output = BytesIO()
    canvas = Canvas(output)
    canvas.drawString(40, 790, "Quotation")
    canvas.drawString(40, 740, "Quantity booked 50 | Anchor bolts | measure pcs")
    canvas.showPage()
    canvas.save()
    with TestClient(create_app(tmp_path / "workspace.sqlite")) as client:
        batch = create(client)
        route = f"/batches/{batch}/documents"
        key = ok(client.post(route, files={"file": ("unfamiliar.pdf", output.getvalue())}))["id"]
        view = ok(client.get(f"/batches/{batch}"))
        assert view["ai"] == {"provider": "gemini", "configured": True}
        assert not requests and view["documents"][0]["role"] == "unknown"
        ok(client.post(f"/documents/{key}/retry", data={"use_gemini": "true"}))
        view = ok(client.get(f"/batches/{batch}"))
        doc, = view["documents"]
        assert len(requests) == 1 and doc["role"] == "quote"
        assert doc["provenance"]["provider"] == "gemini"
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
