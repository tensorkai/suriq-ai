"""Extract bounded, immutable source snapshots and field provenance."""
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from openpyxl import load_workbook
from openpyxl.utils import coordinate_to_tuple, get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

MAX_PAGES = int(os.getenv("SURIQ_MAX_PAGES", "50"))
MAX_ROWS = int(os.getenv("SURIQ_MAX_ROWS", "500"))
MAX_COLUMNS = 64
OCR_CONFIDENCE = int(os.getenv("SURIQ_OCR_CONFIDENCE", "75"))
NS = "{http://www.w3.org/1999/xhtml}"

LINE_FIELDS = {
    "code": "item id|bom item id|id|item no|item number|code|part no|part number|sku",
    "material": "material|material name|item|quoted material|item name|product",
    "description": "description|item description|particulars|materials description",
    "quantity": "quantity|qty|q'ty",
    "unit": "unit|uom|unit of measure",
    "specification": "specification|specifications|spec|technical specifications",
    "dimension": "size|dimension|dimensions|diameter|thickness",
    "grade": "grade|material grade",
    "standard": "standard|required standard|pns|astm",
    "brand": "brand|manufacturer|required brand|mandatory brand",
    "preferred_brand": "preferred brand",
    "pack": "pack|pack size|packaging|packaging size",
    "unit_price": "unit price|unit cost|price|rate|estimated unit cost|unit price php",
    "amount": "line total|total price|amount|total|line amount|amount php",
    "delivery_date": "delivery date|needed by|required delivery date",
    "lead_time": "lead time|delivery lead time",
}

TERM_FIELDS = {
    "supplier": "supplier|supplier name|vendor",
    "reference": "reference|quotation no|quote no|quotation number|quote reference",
    "quote_date": "date|quote date|quotation date",
    "validity": "validity|valid for|valid until|expiry|expiration|validity period",
    "currency": "currency",
    "delivery": "delivery|lead time|delivery lead time|delivery date|needed by|required delivery date",
    "payment": "payment|payment terms|terms of payment",
    "freight": "freight|shipping|delivery fee|delivery charge|transport|transport fee",
    "charges": "charges|additional charges|extra charges|other charges",
    "discount": "discount",
    "tax": "tax|tax basis|vat status|vat basis|vat treatment",
    "vat": "vat|vat amount|12% vat|vat 12%",
    "subtotal": "subtotal|sub total",
    "total": "grand total|total payable|total amount due|total",
}

ATTRIBUTES = {
    "dimension": r"\b\d+(?:\.\d+)?\s*(?:x\s*\d+(?:\.\d+)?\s*){0,2}(?:mm|cm|in(?:ch(?:es)?)?)\b",
    "grade": r"\b(?:grade|gr\.?)\s*[A-Z0-9]+(?:[-/]\d+)?\b",
    "standard": r"\b(?:PNS|ASTM|ISO)\s*[A-Z]?\s*\d+[\w:.-]*\b",
    "pack": r"\b\d+(?:\.\d+)?\s*(?:kg|g|l)\s*(?:/\s*|per\s+)(?:bag|sack|pack|set|roll|pail)\b",
}
UNIT_PATTERN = (
    r"pcs?|pieces?|bags?|sacks?|lengths?|sets?|rolls?|pail[s]?|packs?|kg|g|MT|tonnes?|"
    r"mm|cm|m|sqm|sq\.?\s*m|m²|m2|cu\.?\s*m|m³|m3|l|liters?"
)
NUMBER_PATTERN = r"[\d,]+(?:\.\d+)?"
FALLBACK_LINE = re.compile(
    rf"^(?:(?P<code>[A-Z]+[-.]\d+)\s+)?(?P<material>.+?)\s+"
    rf"(?P<quantity>{NUMBER_PATTERN})\s+(?P<unit>{UNIT_PATTERN})"
    rf"(?:\s+(?:PHP\s*|₱)?(?P<unit_price>{NUMBER_PATTERN})\s+"
    rf"(?:PHP\s*|₱)?(?P<amount>{NUMBER_PATTERN}))?$", re.I,
)


class ExtractionError(ValueError):
    pass


def aliases(mapping):
    return {label: field for field, names in mapping.items() for label in names.split("|")}


LINE_ALIASES = aliases(LINE_FIELDS)
TERM_ALIASES = aliases(TERM_FIELDS)


def label(text):
    return re.sub(r"[.():#₱]", " ", text.casefold()).strip().replace("  ", " ")


def run_command(args, timeout=45):
    try:
        return subprocess.run(args, check=True, capture_output=True, timeout=timeout,
                              env={**os.environ, "LC_ALL": "C"}).stdout
    except FileNotFoundError as exc:
        raise ExtractionError(f"Install {Path(args[0]).name} to process this source.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ExtractionError(f"{Path(args[0]).name} timed out on this source.") from exc
    except subprocess.CalledProcessError as exc:
        raise ExtractionError(f"{Path(args[0]).name} could not read this source.") from exc


def validate_file(name, content):
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" and re.match(rb"%PDF-\d\.\d", content):
        return "pdf"
    if suffix == ".xlsx" and content.startswith(b"PK\x03\x04"):
        from io import BytesIO
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if (len(entries) > 2500 or sum(entry.file_size for entry in entries) > 40_000_000
                        or any(entry.flag_bits & 1 for entry in entries)):
                    raise ExtractionError("Workbook exceeds the safe expanded size or is encrypted.")
                if {"[Content_Types].xml", "xl/workbook.xml"} <= names and not any(
                    "vbaProject" in name for name in names
                ):
                    return "xlsx"
        except BadZipFile:
            pass
    raise ExtractionError("Upload a valid PDF or an unencrypted XLSX workbook.")


def render_page(path, page, directory, *, scan=False):
    target = str(Path(directory) / ("scan" if scan else "page"))
    scale = "2400" if scan else "1800"
    run_command(["pdftoppm", "-f", str(page), "-l", str(page), "-cropbox", "-png",
                 "-singlefile", "-scale-to", scale, str(path), target])
    result = Path(target + ".png")
    if not result.is_file():
        raise ExtractionError("The PDF page could not be rendered.")
    return result


def ocr_words(path, page, directory):
    import csv
    from io import StringIO
    from struct import unpack

    image = render_page(path, page, directory, scan=True)
    with image.open("rb") as file:
        width, height = unpack(">II", file.read(24)[16:24])
    output = run_command([os.getenv("SURIQ_TESSERACT", "tesseract"), str(image), "stdout",
                          "-l", os.getenv("SURIQ_OCR_LANGUAGE", "eng"), "--psm", "11", "tsv"])
    words = []
    for row in csv.DictReader(StringIO(output.decode("utf-8", "replace")), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        x, y, w, h = [int(row[key]) for key in ("left", "top", "width", "height")]
        words.append((row["text"].strip(), (x / width, y / height, w / width, h / height),
                      float(row["conf"])))
    return words


def word_anchor(source, words):
    boxes = [list(box) for _, box, _ in words]
    return {"source": source, "boxes": boxes, "text": " ".join(word[0] for word in words),
            "weak": any(word[2] < OCR_CONFIDENCE for word in words)}


def row_groups(words):
    """Rebuild horizontal rows across independently detected PDF/OCR columns."""
    rows = []
    for word in sorted(words, key=lambda word: word[1][1] + word[1][3] / 2):
        _, (_, top, _, height), _ = word
        center = top + height / 2
        if rows and abs(rows[-1][0] - center) <= max(.003, min(rows[-1][1], height) * .55):
            rows[-1][2].append(word)
        else:
            rows.append((center, height, [word]))
    return [sorted(row, key=lambda word: word[1][0]) for _, _, row in rows]


def words_in_span(words, start, end):
    offset = 0
    selected = []
    for word in words:
        stop = offset + len(word[0])
        if stop > start and offset < end:
            selected.append(word)
        offset = stop + 1
    return selected


def derive_attributes(fields, anchors, evidence_words=None):
    for field, pattern in ATTRIBUTES.items():
        if fields.get(field):
            continue
        for source_field in ("specification", "description", "material"):
            match = re.search(pattern, fields.get(source_field, ""), re.I)
            if match:
                fields[field] = match.group()
                anchor = anchors[source_field]
                words = (evidence_words or {}).get(source_field)
                anchors[field] = (
                    word_anchor(anchor["source"], words_in_span(words, *match.span()))
                    if words else anchor
                )
                break


def make_line(source, index, fields, anchors, words=None):
    if not fields.get("material") and not fields.get("description"):
        return None
    derive_attributes(fields, anchors, words)
    return {"id": f"{source}:{index}", "fields": fields, "anchors": anchors,
            "original": " | ".join(dict.fromkeys(a["text"] for a in anchors.values()))}


def term_from_cells(cells, make_anchor):
    """Return a commercial term from a literal label and an explicit value."""
    if not cells:
        return None
    name, separator, tail = cells[0][0].partition(":")
    field = TERM_ALIASES.get(label(name))
    if field:
        if separator and tail.strip():
            value, evidence = tail.strip(), [cells[0]]
        elif len(cells) > 1:
            value, evidence = cells[1][0], cells[1:2]
        else:
            return None
        if field == "vat" and not re.search(r"\d", value):
            field = "tax"
        return field, value, make_anchor(evidence)
    combined = " ".join(cell[0] for cell in cells)
    if re.fullmatch(r"(?:12\s*%\s*)?(?:VAT[ -]?(?:inclusive|exclusive|included|excluded)|non[ -]?VAT)",
                    combined, re.I):
        return "tax", combined, make_anchor(cells)
    return None


def add_term(snapshot, term):
    field, value, anchor = term
    mentions = snapshot["term_mentions"].setdefault(field, [])
    mentions.append({"value": value, "anchor": anchor})
    snapshot["terms"].setdefault(field, value)
    snapshot["term_anchors"].setdefault(field, anchor)


def column_headers(words):
    columns = []
    index = 0
    while index < len(words):
        for length in range(min(4, len(words) - index), 0, -1):
            chunk = words[index:index + length]
            field = LINE_ALIASES.get(label(" ".join(word[0] for word in chunk)))
            if field:
                columns.append((chunk[0][1][0], field))
                index += length
                break
        else:
            index += 1
    names = {field for _, field in columns}
    if names & {"material", "description"} and names & {"quantity", "unit", "unit_price"}:
        return columns
    return []


def pdf_rows(snapshot, source, words, inherited):
    columns = inherited
    for index, row in enumerate(row_groups(words), 1):
        raw = " ".join(word[0] for word in row)
        if len(raw) > 4000:
            continue
        header = column_headers(row)
        if header:
            columns = header
            continue
        term = re.match(r"^([^:]{1,45}):\s*(.+)$", raw)
        if term and (field := TERM_ALIASES.get(label(term[1]))):
            if field == "vat" and re.search(r"inclus|exclus|non[ -]?vat", term[2], re.I):
                field = "tax"
            add_term(snapshot, (field, term[2], word_anchor(source, words_in_span(row, *term.span(2)))))
            continue
        table_term = term_from_cells([(raw, None)], lambda _: word_anchor(source, row))
        if table_term:
            add_term(snapshot, table_term)
            continue

        matched_term = False
        for alias in sorted(TERM_ALIASES, key=len, reverse=True):
            match = re.fullmatch(rf"{re.escape(alias)}\s+[₱ ]*({NUMBER_PATTERN})", raw, re.I)
            if match and TERM_ALIASES[alias] in {"subtotal", "total", "vat", "freight", "discount"}:
                add_term(snapshot, (TERM_ALIASES[alias], match[1],
                                    word_anchor(source, words_in_span(row, *match.span(1)))))
                matched_term = True
                break
        if matched_term:
            continue

        fields, groups = {}, {}
        if columns:
            for word in row:
                x = word[1][0]
                eligible = [field for start, field in columns if start - .012 <= x]
                if eligible:
                    groups.setdefault(eligible[-1], []).append(word)
            fields = {key: " ".join(word[0] for word in value) for key, value in groups.items()}
        # A row without an identity and a populated numeric/unit cell is not an inferred material.
        if not (fields.keys() & {"material", "description"} and
                any(re.fullmatch(NUMBER_PATTERN, fields.get(k, "")) for k in ("quantity", "amount", "unit_price"))):
            match = FALLBACK_LINE.fullmatch(raw)
            if not match:
                continue
            fields = {key: value for key, value in match.groupdict().items() if value}
            groups = {key: words_in_span(row, *match.span(key)) for key in fields}
        anchors = {key: word_anchor(source, words) for key, words in groups.items()}
        if line := make_line(source, index, fields, anchors, groups):
            snapshot["lines"].append(line)
    return columns


def extract_pdf(path, snapshot, directory):
    metadata = run_command(["pdfinfo", str(path)]).decode("utf-8", "replace")
    pages = re.search(r"^Pages:\s*(\d+)", metadata, re.M)
    if not pages or not 1 <= int(pages[1]) <= MAX_PAGES:
        raise ExtractionError(f"PDF must contain 1–{MAX_PAGES} pages.")
    output = run_command(["pdftotext", "-bbox-layout", "-cropbox", str(path), "-"])
    if len(output) > 25_000_000:
        raise ExtractionError("The PDF text layer exceeds the extraction limit.")
    root = ElementTree.fromstring(output)
    box_metadata = run_command(["pdfinfo", "-f", "1", "-l", pages[1], "-box", str(path)])
    rotations = {int(page): int(angle) for page, angle in re.findall(
        rb"^Page\s+(\d+) rot:\s*(-?\d+)", box_metadata, re.M
    )}
    columns = []
    for page, element in enumerate(root.iter(f"{NS}page"), 1):
        w, h = float(element.get("width")), float(element.get("height"))
        if rotations.get(page, 0) % 180:
            w, h = h, w
        words = []
        for word in element.iter(f"{NS}word"):
            if not word.text or not word.text.strip():
                continue
            x1, y1, x2, y2 = (float(word.get(key)) for key in ("xMin", "yMin", "xMax", "yMax"))
            words.append((word.text.strip(), (x1/w, y1/h, (x2-x1)/w, (y2-y1)/h), 100))
        source = {"ref": page, "width": w, "height": h, "method": "text", "warning": ""}
        if not words:
            source["method"] = "ocr"
            try:
                words = ocr_words(path, page, directory)
            except ExtractionError as exc:
                source["warning"] = str(exc)
        if not words and not source["warning"]:
            source["warning"] = "Unreadable page. Inspect the original before relying on this offer."
        elif any(word[2] < OCR_CONFIDENCE for word in words):
            source["warning"] = "Low-confidence OCR. Check the extracted values against this page."
        snapshot["sources"].append(source)
        columns = pdf_rows(snapshot, page, words, columns)
    if len(snapshot["sources"]) != int(pages[1]):
        raise ExtractionError("Could not index every PDF page.")


def validate_workbook_grid(path):
    # Inspect actual cell coordinates. XLSX dimension declarations can be stale
    # and bounded iter_rows alone would silently omit distant populated cells.
    with ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith("xl/worksheets/") or not name.endswith(".xml"):
                continue
            with archive.open(name) as stream:
                for _, node in ElementTree.iterparse(stream, events=("end",)):
                    if node.tag.endswith("}c") and node.get("r"):
                        row, col = coordinate_to_tuple(node.get("r"))
                        if row > MAX_ROWS or col > MAX_COLUMNS:
                            raise ExtractionError(f"Workbook exceeds {MAX_ROWS} rows or {MAX_COLUMNS} columns.")
                    node.clear()


def extract_workbook(path, snapshot):
    validate_workbook_grid(path)
    workbook = load_workbook(path, data_only=False, read_only=True, keep_links=False)
    cached = None
    try:
        cached = load_workbook(path, data_only=True, read_only=True, keep_links=False)
        if len(workbook.worksheets) > 50:
            raise ExtractionError("Workbook contains more than 50 worksheets.")
        total = 0
        for sheet in workbook.worksheets:
            source = sheet.title
            sheet.reset_dimensions()
            cached[source].reset_dimensions()
            columns = {}
            grid = []
            warning = ""
            for row_no, (raw_row, data_row) in enumerate(zip(
                sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS),
                cached[source].iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS),
            ), 1):
                raw = {get_column_letter(i): str(cell.value)[:4000]
                       for i, cell in enumerate(raw_row, 1) if cell.value is not None}
                if not raw:
                    continue
                total += 1
                if total > MAX_ROWS:
                    raise ExtractionError(f"Workbook exceeds {MAX_ROWS} rows or {MAX_COLUMNS} columns.")
                values = {get_column_letter(i): str(cell.value)[:4000]
                          for i, cell in enumerate(data_row, 1)
                          if cell.value is not None and cell.data_type != "e"}
                if raw.keys() - values.keys():
                    warning = "Some cells contain a formula without a usable saved result or a spreadsheet error."
                grid.append({"row": row_no, "cells": raw})

                def anchor(items):
                    return {"source": source, "cells": [f"{col}{row_no}" for _, col in items],
                            "text": " | ".join(raw[col] for _, col in items), "weak": False}

                header = {col: LINE_ALIASES[label(value)] for col, value in values.items()
                          if label(value) in LINE_ALIASES}
                if set(header.values()) & {"material", "description"} and set(header.values()) & {
                    "quantity", "unit", "unit_price"
                }:
                    columns = header
                    continue
                term = term_from_cells([(value, col) for col, value in values.items()], anchor)
                if term:
                    add_term(snapshot, term)
                    continue
                fields = {field: values[col] for col, field in columns.items() if col in values}
                anchors = {field: anchor([(values[col], col)]) for col, field in columns.items()
                           if col in values}
                if line := make_line(source, row_no, fields, anchors):
                    snapshot["lines"].append(line)
            snapshot["sources"].append({"ref": source, "method": "cells", "warning": warning, "rows": grid})
    finally:
        workbook.close()
        if cached is not None:
            cached.close()


def extract(content, kind):
    snapshot = {"sources": [], "lines": [], "terms": {}, "term_anchors": {}, "term_mentions": {}}
    with TemporaryDirectory(prefix="suriq-") as directory:
        path = Path(directory) / f"source.{kind}"
        path.write_bytes(content)
        try:
            if kind == "pdf":
                extract_pdf(path, snapshot, directory)
            else:
                extract_workbook(path, snapshot)
        except ExtractionError:
            raise
        except (BadZipFile, InvalidFileException, ElementTree.ParseError, OSError, KeyError, ValueError) as exc:
            raise ExtractionError("The source could not be decoded. Check the file and retry.") from exc
    return snapshot
