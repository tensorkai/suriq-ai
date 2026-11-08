from io import BytesIO

from openpyxl import Workbook
from reportlab.pdfgen.canvas import Canvas

HEADERS = ["Item ID", "Material", "Qty", "Unit", "Unit price", "Amount"]
BOM = ["B-014", "Deformed bar 16 mm Grade 60", "80", "pcs", "", ""]
OFFER = ["B-014", "Deformed bar 12 mm Grade 60", "80", "pcs", "100", "8000"]
TERMS = ["Subtotal: 8000", "Tax: VAT inclusive", "Freight: Included", "Discount: None"]
COLUMNS = [40, 103, 320, 362, 410, 498]


def pdf(rows, page=1, pages=3, terms=(), blank_page=None):
    output = BytesIO()
    canvas = Canvas(output)
    for index in range(1, pages + 1):
        if index != blank_page:
            canvas.drawString(40, 790, f"Procurement document / {index}")
        if index == page:
            for offset, values in enumerate([HEADERS, *rows]):
                for x, value in zip(COLUMNS, values):
                    canvas.drawString(x, 725 - offset * 19, str(value))
            for offset, term in enumerate(terms):
                canvas.drawString(40, 670 - (len(rows) + offset) * 19, term)
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def xlsx(rows, terms=()):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Materials"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    for term in terms:
        key, value = term.split(": ", 1)
        sheet.append([key, value])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def scan(blob=None):
    from subprocess import run
    from tempfile import TemporaryDirectory
    from pathlib import Path

    from PIL import Image
    from reportlab.lib.utils import ImageReader

    if blob is None:
        image = Image.new("RGB", (1240, 1754), "white")
        image.save(output := BytesIO(), "PNG")
        image_data = output.getvalue()
    else:
        with TemporaryDirectory() as temp:
            original = Path(temp) / "original.pdf"
            original.write_bytes(blob)
            run(["pdftoppm", "-f", "1", "-l", "1", "-r", "170", "-png", "-singlefile",
                 str(original), str(Path(temp) / "scan")], check=True)
            image_data = (Path(temp) / "scan.png").read_bytes()
    output = BytesIO()
    canvas = Canvas(output)
    width, height = canvas._pagesize
    canvas.drawImage(ImageReader(BytesIO(image_data)), 0, 0, width, height)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def docx(paragraphs, rows):
    from xml.sax.saxutils import escape
    from zipfile import ZipFile

    def paragraph(text):
        return f"<w:p><w:r><w:t>{escape(str(text))}</w:t></w:r></w:p>"

    table = "<w:tbl>" + "".join("<w:tr>" + "".join(
        f"<w:tc>{paragraph(value)}</w:tc>" for value in row
    ) + "</w:tr>" for row in rows) + "</w:tbl>"
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
        </Types>""")
        archive.writestr("_rels/.rels", """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
        </Relationships>""")
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                         + "".join(map(paragraph, paragraphs)) + table + "<w:sectPr/></w:body></w:document>")
    return output.getvalue()
