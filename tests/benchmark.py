"""Timed synthetic 40-page intake and verification. Fixture generation is excluded."""
from io import BytesIO
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from time import perf_counter

from fastapi.testclient import TestClient
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from service.api import create_app
from tests.fixtures import COLUMNS, HEADERS
from tests.test_acceptance import create, ok, upload


def make_document(supplier):
    output = BytesIO()
    canvas = Canvas(output)
    for page in range(1, 11):
        canvas.drawString(40, 790, f"Procurement schedule / Page {page}")
        for x, heading in zip(COLUMNS, HEADERS):
            canvas.drawString(x, 725, heading)
        row = [f"B-{page:03}", f"Deformed bar {12 if page == 2 and supplier in ('A', 'B') else 16} mm",
               "80", "pcs", "100" if supplier else "", "8000" if supplier else ""]
        for x, value in zip(COLUMNS, row):
            canvas.drawString(x, 706, value)
        if page == 10 and supplier:
            for index, term in enumerate(["Subtotal: 80000", "Tax: VAT inclusive", "Freight: Included",
                                          "Discount: None", "Total: 80000"]):
                canvas.drawString(40, 660 - index * 18, term)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def rasterize(source, directory):
    path = directory / "original.pdf"
    path.write_bytes(source)
    run(["pdftoppm", "-r", "150", "-png", str(path), str(directory / "raster")], check=True)
    output = BytesIO()
    canvas = Canvas(output)
    for page in sorted(directory.glob("raster-*.png")):
        canvas.drawImage(ImageReader(str(page)), 0, 0, *canvas._pagesize)
        canvas.showPage()
        page.unlink()
    canvas.save()
    return output.getvalue()


def benchmark(sources, directory, kind):
    with TestClient(create_app(directory / f"{kind}.sqlite")) as client:
        batch = create(client)
        start = perf_counter()
        for supplier, content in sources.items():
            upload(client, batch, f"{supplier or 'BOM'}.pdf", content, supplier)
        ok(client.patch(f"/batches/{batch}/scope", json={"mode": "all"}))
        data = ok(client.post(f"/batches/{batch}/verify"))
        elapsed = perf_counter() - start
    pages = sum(len(doc["sources"]) for doc in data["documents"])
    extracted = sum(len(doc["lines"]) for doc in data["documents"])
    dimensions = [issue for issue in data["issues"] if issue["rule"] == "dimension"]
    assert pages == 40
    assert extracted == 40, [len(doc["lines"]) for doc in data["documents"]]
    assert len(dimensions) == 1 and len(dimensions[0]["evidence"]) == 3
    print(f"{kind}: {pages} pages, {extracted} material rows, 1 dimension group across 3 sources, {elapsed:.2f} s",
          flush=True)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="suriq-benchmark-") as tmp:
        directory = Path(tmp)
        sources = {supplier: make_document(supplier) for supplier in ("", "A", "B", "C")}
        benchmark(sources, directory, "native PDF")
        benchmark({key: rasterize(value, directory) for key, value in sources.items()}, directory, "scanned PDF")
