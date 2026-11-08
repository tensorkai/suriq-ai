"""Opt-in live intake smoke test using only generated documents."""
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from service.inference import provider
from tests.fixtures import BOM, xlsx
from tests.test_acceptance import create, ok


def main():
    use_gemini = provider() == "gemini"
    if not use_gemini and os.getenv("SURIQ_TEST_TRACE_MODEL") == "1":
        from service import ollama
        original_request = ollama.request_json

        def trace(path, body=None, **kwargs):
            response = original_request(path, body, **kwargs)
            if path == "/api/chat":
                print("Synthetic model output:", response.get("message", {}).get("content", ""), flush=True)
            return response

        ollama.request_json = trace
    output = BytesIO()
    page = Canvas(output)
    page.drawString(45, 780, "Supplier quotation")
    page.drawString(45, 750, "Supplier: Synthetic Foundry")
    page.drawString(45, 700, "Anchor bolts B-014 | ordered: 80 pcs | per piece: PHP 100")
    page.drawString(45, 675, "Extended amount for the above lot: PHP 8000")
    page.showPage()
    page.save()
    start = monotonic()
    with TemporaryDirectory(prefix="suriq-live-") as temp:
        with TestClient(create_app(Path(temp) / "test.sqlite")) as client:
            batch = create(client)
            route = f"/batches/{batch}/documents"
            ok(client.post(route, files={"file": ("requirements.xlsx", xlsx([BOM]))}))
            key = ok(client.post(route, files={"file": ("unfamiliar.pdf", output.getvalue())}))["id"]
            data = ok(client.get(f"/batches/{batch}"))
            doc = next(doc for doc in data["documents"] if doc["id"] == key)
            print(f"Live intake in {monotonic() - start:.1f}s. Role={doc['role']} status={doc['status']}")
            print(f"Provenance={doc.get('provenance')} error={doc['error']}")
            print(f"Material fields={[row['fields'] for row in doc['lines']]}")
            assert doc["provenance"]["inference"] == "processed", doc["error"]
            assert doc["role"] == "quote" and doc["lines"], doc["classification"]
            assert any(line["fields"].get("quantity") == "80" for line in doc["lines"])
            if use_gemini:
                from service.verification import decimal_value
                assert any(decimal_value(line["fields"].get("amount", "")) == 8000 for line in doc["lines"])
            for line in doc["lines"]:
                assert all(anchor["boxes"] and anchor["weak"] for anchor in line["anchors"].values())
            ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
            result = ok(client.post(f"/batches/{batch}/verify"))
            assert result["comparison"][0]["total"] is None
            assert client.get(f"/documents/{key}/page/1").content.startswith(b"\x89PNG")
            assert client.get(f"/batches/{batch}/export/csv").is_success
            print(f"Live {provider()} intake, guarded verification, PDF preview, and CSV export passed.")


if __name__ == "__main__":
    main()
