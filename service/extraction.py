"""Extract bounded, immutable source snapshots and field provenance."""
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from openpyxl import __version__ as openpyxl_version, load_workbook
from openpyxl.utils import coordinate_to_tuple, get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException

from service.classification import OFFER, REQUIREMENTS, UNSUPPORTED

MAX_PAGES = int(os.getenv("SURIQ_MAX_PAGES", "50"))
MAX_ROWS = int(os.getenv("SURIQ_MAX_ROWS", "500"))
MAX_COLUMNS = 64
OCR_CONFIDENCE = int(os.getenv("SURIQ_OCR_CONFIDENCE", "75"))
NS = "{http://www.w3.org/1999/xhtml}"
PARSER_VERSION = "native-2.3"

LINE_FIELDS = {
    "code": "item id|bom item id|id|item no|item number|no|code|part no|part number|sku",
    "material": "material|material name|item|quoted material|item name|product|material particulars|item specification|article",
    "description": "description|item description|particulars|materials description",
    "quantity": "quantity|qty|q'ty|required quantity|required qty|qty required|order quantity|offered quantity|volume",
    "unit": "unit|uom|u/m|unit of measure|measure",
    "specification": "specification|specifications|spec|technical specifications",
    "dimension": "size|dimension|dimensions|diameter|thickness",
    "grade": "grade|material grade",
    "standard": "standard|required standard|pns|astm",
    "brand": "brand|manufacturer|required brand|mandatory brand",
    "preferred_brand": "preferred brand",
    "pack": "pack|pack size|packaging|packaging size",
    "unit_price": "unit price|unit cost|price|rate|estimated unit cost|estimated unit price|unit price php|price per unit",
    "amount": "line total|total price|amount|total|line amount|amount php|extended|extended amount|extension",
    "labor_unit_price": "labor unit rate|labour unit rate|labor unit price",
    "labor_amount": "labor extension|labour extension",
    "line_total": "combined line total",
    "delivery_date": "delivery date|needed by|required delivery date",
    "lead_time": "lead time|delivery lead time",
}

TERM_FIELDS = {
    "supplier": "supplier|supplier name|vendor|quoted by",
    "reference": "reference|quotation no|quote no|quotation number|quote reference",
    "quote_date": "date|quote date|quotation date",
    "validity": "validity|valid for|valid until|expiry|expiration|validity period|offer valid until|offer validity|price validity",
    "currency": "currency",
    "delivery": "delivery|indicative material delivery|material delivery|lead time|delivery lead time|delivery date|needed by|required delivery date",
    "payment": "payment|payment terms|terms of payment",
    "freight": "freight|shipping|delivery fee|delivery charge|transport|transport fee",
    "charges": "charges|additional charges|extra charges|other charges",
    "discount": "discount",
    "tax": "tax|tax basis|vat status|vat basis|vat treatment",
    "vat": "vat|vat amount|12% vat|vat 12%",
    "subtotal": "subtotal|sub total",
    "total": "grand total|total payable|total amount due|total|total quotation|quotation total",
    "materials_total": "materials|materials total|total materials|material cost|supplier stated materials total",
    "item_extensions_total": "sum of item extensions|item extensions total",
    "labor_equipment": "labor, equipment, tools|labor and equipment|labor and tools",
    "mobilization": "mobilization and consumables|mobilization",
    "advance_payment": "advance payment|down payment",
    "balance_payment": "balance upon completion|balance payment",
    "draft_notice": "draft notice",
}

ATTRIBUTES = {
    "dimension": r"\b\d+(?:\.\d+)?\s*(?:x\s*\d+(?:\.\d+)?\s*){0,2}(?:mm|cm|in(?:ch(?:es)?)?)\b",
    "grade": r"\b(?:grade|gr\.?)\s*[A-Z0-9]+(?:[-/]\d+)?\b",
    "standard": r"\b(?:PNS|ASTM|ISO)\s*[A-Z]?\s*\d+[\w:.-]*\b",
    "pack": r"\b\d+(?:\.\d+)?\s*(?:kg|g|l)\s*(?:/\s*|per\s+)(?:bag|sack|pack|set|roll|pail)\b",
}
UNIT_PATTERN = (
    r"pcs?|pieces?|bags?|sacks?|lengths?|sets?|rolls?|pail[s]?|packs?|kg|g|MT|tonnes?|"
    r"lots?|units?|ft|feet|mm|cm|m|sqm|sq\.?\s*m|m²|m2|cu\.?\s*m|m³|m3|l|liters?"
)
NUMBER_PATTERN = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
PRICE_PATTERN = rf"(?:(?:Php|₱)\s*)?{NUMBER_PATTERN}"
MATERIAL_ROW = (
    rf"(?P<material>.+?)\s+(?P<quantity>{NUMBER_PATTERN})\s+(?P<unit>{UNIT_PATTERN})"
    rf"(?:\s+(?P<unit_price>{PRICE_PATTERN})\s+(?P<amount>{PRICE_PATTERN}))?"
)
FALLBACK_LINE = re.compile(rf"^(?:(?P<code>[A-Z]+[-.]\d+)\s+)?{MATERIAL_ROW}$", re.I)
NUMBERED_LINE = re.compile(rf"^(?P<code>\d+|[A-Z]+[-.]\d+)\s+{MATERIAL_ROW}$", re.I)
PDF_MONEY_FIELDS = {"subtotal", "total", "vat", "freight", "discount", "materials_total",
                    "item_extensions_total", "labor_equipment", "mobilization", "advance_payment", "balance_payment"}

# In this BOQ layout the upper header identifies cost categories and the
# lower header distinguishes unit rates from extensions.
SPLIT_HEADER = re.compile(
    r"^(?:no|item no)\s+(?:specification / work item|description|work item)\s+(?:qty|quantity)\s+unit\s+"
    r"material labou?r material labou?r line$", re.I,
)
SPLIT_SUBHEADER = re.compile(r"^unit rate unit rate extension extension total$", re.I)
DRAFT_MARK = re.compile(r"reconstructed\s*/\s*not issued", re.I)
SCHEDULE_PRICE_FIELDS = ("unit_price", "labor_unit_price", "amount", "labor_amount", "line_total")
SCHEDULE_ROW = re.compile(
    rf"^(?P<code>\d+|[A-Z]+[-.]\d+)\s+(?P<material>.+?)\s+(?P<quantity>{NUMBER_PATTERN})\s+"
    rf"(?P<unit>{UNIT_PATTERN})" +
    "".join(rf"\s+(?P<{field}>{PRICE_PATTERN}|[-–—])" for field in SCHEDULE_PRICE_FIELDS) + "$", re.I,
)
SCHEDULE_TOTAL_LABELS = {
    "materials extension", "extended material costs", "extended labor / site services",
    "extended labour / site services", "total detailed schedule", "quoted project total",
    "unclassified difference to quotation", "non-schedule profit / tax provisions",
}


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
    if suffix in {".xlsx", ".docx"} and content.startswith(b"PK\x03\x04"):
        from io import BytesIO
        try:
            with ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if (len(entries) > 2500 or sum(entry.file_size for entry in entries) > 40_000_000
                        or len(names) != len(entries) or any(
                            entry.flag_bits & 1 or entry.filename.startswith(("/", "\\"))
                            or ".." in entry.filename.replace("\\", "/").split("/")
                            for entry in entries)):
                    raise ExtractionError("The Office file exceeds safe archive limits or is encrypted.")
                if {"[Content_Types].xml", "word/document.xml"} <= names and suffix == ".docx" and not any(
                    "vbaproject" in name.lower() for name in names
                ):
                    return "docx"
                if suffix == ".xlsx" and {"[Content_Types].xml", "xl/workbook.xml"} <= names and not any(
                    "vbaproject" in name.lower() for name in names
                ):
                    return "xlsx"
        except BadZipFile:
            pass
    raise ExtractionError("Upload a valid PDF, XLSX workbook, or DOCX document.")


def render_page(path, page, directory, *, scan=False, scale=None):
    target = str(Path(directory) / ("scan" if scan else "page"))
    scale = str(scale or (2400 if scan else 1800))
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


def keep_heading(snapshot, anchor, near_start):
    if near_start or any(pattern.search(anchor["text"]) for pattern in (REQUIREMENTS, OFFER, UNSUPPORTED)):
        snapshot["headings"].append(anchor)


def pdf_term(raw):
    if re.fullmatch(r"(?:reconstructed|draft)\s*[/–-]\s*not issued", raw, re.I):
        return "draft_notice", raw, (0, len(raw))
    if re.match(r"^VAT treatment was not (?:separately )?specified\b", raw, re.I):
        return "tax", raw, (0, len(raw))
    money = re.fullmatch(rf"(?P<label>.+?)\s+(?P<value>{PRICE_PATTERN})", raw, re.I)
    if money:
        name = re.sub(r"\s+\([^()]*\)$", "", money["label"].rstrip(":"))
        name = label(name.replace("-", " "))
        field = TERM_ALIASES.get(name)
        if re.fullmatch(r"sum of \d+ item extensions", name):
            field = "item_extensions_total"
        if field in PDF_MONEY_FIELDS:
            return field, money["value"], money.span("value")
    term = re.match(r"^([^:]{1,45}):\s*(.+)$", raw)
    if term and (field := TERM_ALIASES.get(label(term[1]))):
        value = term[2]
        if field == "vat" and re.search(r"inclus|exclus|non[ -]?vat", value, re.I):
            field = "tax"
        if field == "currency":
            value = value.split("|", 1)[0].strip()
        return field, value, (term.start(2), term.start(2) + len(value))
    return None


def split_schedule(snapshot, source, rows):
    """Read a priced BOQ only when both levels of its header agree."""
    texts = [" ".join(word[0] for word in row) for row in rows]
    header = next((i for i, raw in enumerate(texts[:-1]) if SPLIT_HEADER.fullmatch(label(raw))
                   and SPLIT_SUBHEADER.fullmatch(label(texts[i + 1]))), None)
    if header is None:
        return False

    ref = source["ref"]
    issuer = site = ""
    for index, raw in enumerate(texts[:header]):
        keep_heading(snapshot, word_anchor(ref, rows[index]), True)
        if draft := DRAFT_MARK.search(raw):
            issuer = issuer or raw[:draft.start()].strip()
            add_term(snapshot, ("draft_notice", draft.group(), word_anchor(ref, words_in_span(rows[index], *draft.span()))))
        if location := re.search(r"\bproject site:\s*(.+)$", raw, re.I):
            site = location[1].strip()
        if supplier := re.match(r"^(?:supplier|contractor):\s*(.+)$", raw, re.I):
            issuer = issuer or supplier[1].strip()
    source["schedule"] = " · ".join(part for part in (issuer, site) if part) if issuer else f"Unidentified schedule · page {ref}"
    source["schedule_totals"] = []
    service_page = any(re.search(r"\bnon[- ]material services\b", raw, re.I) for raw in texts[:header])
    count = 0
    for index in range(header + 2, len(rows)):
        raw, row = texts[index], rows[index]
        if match := SCHEDULE_ROW.fullmatch(raw):
            fields = match.groupdict()
            groups = {field: words_in_span(row, *match.span(field)) for field in fields}
            anchors = {field: word_anchor(ref, words) for field, words in groups.items()}
            line = make_line(ref, index + 1, fields, anchors, groups)
            service = service_page or fields["unit_price"] in {"-", "–", "—"} and fields["amount"] in {"-", "–", "—"}
            line.update({"kind": "service" if service else "material", "schedule": source["schedule"]})
            snapshot["lines"].append(line)
            count += 1
            continue
        total = re.fullmatch(rf"(?P<label>.+?)\s+(?P<value>{PRICE_PATTERN})", raw, re.I)
        if total and label(total["label"]) in SCHEDULE_TOTAL_LABELS:
            source["schedule_totals"].append({
                "label": total["label"], "value": total["value"],
                "anchor": word_anchor(ref, words_in_span(row, *total.span("value"))),
            })
        elif re.match(r"^\d+\s", raw) and re.search(rf"\s{NUMBER_PATTERN}\s+(?:{UNIT_PATTERN})\b", raw, re.I):
            source["warning"] = source["warning"] or "A numbered schedule row did not match the printed price columns. Review this page."
    if not count:
        source["warning"] = source["warning"] or "No priced schedule rows were recognized. Review this page."
    return True


def pdf_rows(snapshot, source, rows, inherited):
    columns = inherited
    previous = None
    for index, row in enumerate(rows, 1):
        raw = " ".join(word[0] for word in row)
        if len(raw) > 4000:
            continue
        keep_heading(snapshot, word_anchor(source, row), index <= 8)
        header = column_headers(row)
        if header:
            previous = None
            columns = header
            continue
        term = pdf_term(raw)
        if term:
            field, value, span = term
            add_term(snapshot, (field, value, word_anchor(source, words_in_span(row, *span))))
            continue
        table_term = term_from_cells([(raw, None)], lambda _: word_anchor(source, row))
        if table_term:
            add_term(snapshot, table_term)
            continue

        # Centered headers and right-aligned values do not share left edges.
        # Use the literal column sequence when all six fields have that order.
        order = [field for _, field in columns]
        if (len(order) == 6 and order[0] == "code" and order[1] in {"material", "description"}
                and order[2:] == ["quantity", "unit", "unit_price", "amount"]
                and (numbered := NUMBERED_LINE.fullmatch(raw))):
            fields = {key: value for key, value in numbered.groupdict().items() if value}
            groups = {key: words_in_span(row, *numbered.span(key)) for key in fields}
            anchors = {key: word_anchor(source, words) for key, words in groups.items()}
            line = make_line(source, index, fields, anchors, groups)
            snapshot["lines"].append(line)
            previous = (index, line, groups)
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
                ((fields.get("unit") and re.fullmatch(NUMBER_PATTERN, fields.get("quantity", "")))
                 or any(re.fullmatch(PRICE_PATTERN, fields.get(k, ""), re.I) for k in ("amount", "unit_price")))):
            match = FALLBACK_LINE.fullmatch(raw)
            if not match:
                if previous and index == previous[0] + 1 and len(groups) == 1:
                    field = next(iter(groups))
                    prior_words = previous[2].get(field)
                    if (field in {"material", "description"} and prior_words
                            and row[0][1][1] - prior_words[0][1][1] <= .024
                            and re.match(r"^(?:[0-9]|grade\b|gr\.|ASTM\b|PNS\b|ISO\b)", raw, re.I)):
                        prior_words.extend(row)
                        fields = previous[1]["fields"]
                        anchors = previous[1]["anchors"]
                        fields[field] = " ".join(word[0] for word in prior_words)
                        anchors[field] = word_anchor(source, prior_words)
                        derive_attributes(fields, anchors, previous[2])
                        previous[1]["original"] = " | ".join(dict.fromkeys(a["text"] for a in anchors.values()))
                continue
            fields = {key: value for key, value in match.groupdict().items() if value}
            groups = {key: words_in_span(row, *match.span(key)) for key in fields}
        anchors = {key: word_anchor(source, words) for key, words in groups.items()}
        if line := make_line(source, index, fields, anchors, groups):
            snapshot["lines"].append(line)
            previous = (index, line, groups)
    return columns


def command_version(executable, flag):
    try:
        result = subprocess.run([executable, flag], capture_output=True, timeout=3, check=False)
        return (result.stdout or result.stderr).decode("utf-8", "replace").splitlines()[0][:120]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unavailable"


def extract_pdf(path, snapshot, directory, pdf_words):
    snapshot["provenance"]["poppler"] = command_version("pdftotext", "-v")
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
            if "ocr" not in snapshot["provenance"]:
                snapshot["provenance"]["ocr"] = command_version(os.getenv("SURIQ_TESSERACT", "tesseract"), "--version")
            try:
                words = ocr_words(path, page, directory)
            except ExtractionError as exc:
                source["warning"] = str(exc)
        if not words and not source["warning"]:
            source["warning"] = "Unreadable page. Inspect the original before relying on this offer."
        elif any(word[2] < OCR_CONFIDENCE for word in words):
            source["warning"] = "Low-confidence OCR. Check the extracted values against this page."
        snapshot["sources"].append(source)
        pdf_words[page] = row_groups(words)
        if split_schedule(snapshot, source, pdf_words[page]):
            columns = []
        else:
            columns = pdf_rows(snapshot, page, pdf_words[page], columns)
    if len(snapshot["sources"]) != int(pages[1]):
        raise ExtractionError("Could not index every PDF page.")


def validate_workbook_grid(path):
    # Inspect actual cell coordinates. XLSX dimension declarations can be stale
    # and bounded iter_rows alone would silently omit distant populated cells.
    merges = {}
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
                    if node.tag.endswith("}mergeCell"):
                        span = node.get("ref")
                        col1, row1, col2, row2 = range_boundaries(span)
                        if min(col1, row1) < 1 or row2 > MAX_ROWS or col2 > MAX_COLUMNS:
                            raise ExtractionError("A merged worksheet range exceeds the supported grid.")
                        merges.setdefault(name, []).append((col1, row1, col2, row2))
                    node.clear()
    return merges


def extract_workbook(path, snapshot):
    merges = validate_workbook_grid(path)
    snapshot["provenance"]["openpyxl"] = openpyxl_version
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
            prior = {}
            ranges = merges.get(sheet._worksheet_path, [])
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

                keep_heading(snapshot, anchor([(value, col) for col, value in values.items()]),
                             row_no <= 8)
                headings = dict(values)
                if not columns and prior:
                    for col1, row1, col2, row2 in ranges:
                        origin = get_column_letter(col1)
                        if row1 == row_no - 1 and row2 == row1 and origin in prior:
                            for column in range(col1, col2 + 1):
                                col = get_column_letter(column)
                                combined = label(f"{prior[origin]} {values.get(col, '')}")
                                if combined in LINE_ALIASES:
                                    headings[col] = combined
                        if row1 == row_no - 1 and row2 >= row_no and origin in prior:
                            headings.setdefault(origin, prior[origin])
                header = {col: LINE_ALIASES[label(value)] for col, value in headings.items()
                          if label(value) in LINE_ALIASES}
                prior = values
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
            snapshot["sources"].append({"ref": source, "method": "cells", "warning": warning, "rows": grid,
                                        "merged_ranges": ranges})
    finally:
        workbook.close()
        if cached is not None:
            cached.close()


def extract_docx(path, snapshot):
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with ZipFile(path) as archive:
        body = ElementTree.fromstring(archive.read("word/document.xml")).find(ns + "body")
    if body is None:
        raise ExtractionError("DOCX document body is missing.")

    def text(node):
        return " ".join("".join(run.text or "" for run in para.iter(ns + "t"))
                        for para in node.iter(ns + "p")).strip()

    def add_section(source, rows, paragraph=False):
        grid = []
        columns = {}
        for index, row in enumerate(rows, 1):
            values = {get_column_letter(col): value for col, value in enumerate(row, 1) if value}
            if not values:
                continue
            if len(values) > MAX_COLUMNS or index > MAX_ROWS:
                raise ExtractionError("DOCX source exceeds the supported table size.")
            grid.append({"row": index, "cells": values})

            def anchor(items):
                return {"source": source, "cells": [f"{col}{index}" for _, col in items],
                        "text": " | ".join(value for value, _ in items), "weak": False}

            items = [(value, col) for col, value in values.items()]
            keep_heading(snapshot, anchor(items), paragraph or index <= 3)
            term = term_from_cells(items, anchor)
            if term:
                add_term(snapshot, term)
                continue
            header = {col: LINE_ALIASES[label(value)] for col, value in values.items()
                      if label(value) in LINE_ALIASES}
            if set(header.values()) & {"material", "description"} and set(header.values()) & {"quantity", "unit"}:
                columns = header
                continue
            fields = {field: values[col] for col, field in columns.items() if col in values}
            anchors = {field: anchor([(values[col], col)]) for col, field in columns.items() if col in values}
            if line := make_line(source, index, fields, anchors):
                snapshot["lines"].append(line)
        if grid:
            snapshot["sources"].append({"ref": source, "method": "cells", "warning": "", "rows": grid})

    paragraphs = tables = 0
    for element in body:
        if element.tag == ns + "p":
            paragraphs += 1
            add_section(f"Paragraph {paragraphs}", [[text(element)]], paragraph=True)
        elif element.tag == ns + "tbl":
            tables += 1
            add_section(f"Table {tables}", [[text(cell) for cell in row.findall(ns + "tc")]
                                           for row in element.findall(ns + "tr")])


def extract(content, kind, *, on_inference=lambda: None, use_gemini=False):
    from service.adaptive import assist
    from service.classification import classify

    snapshot = {"headings": [], "sources": [], "lines": [], "terms": {}, "term_anchors": {}, "term_mentions": {},
                "provenance": {"parser": PARSER_VERSION, "inference": "not_required"}}
    pdf_words = {}
    with TemporaryDirectory(prefix="suriq-") as directory:
        path = Path(directory) / f"source.{kind}"
        path.write_bytes(content)
        try:
            if kind == "pdf":
                extract_pdf(path, snapshot, directory, pdf_words)
            elif kind == "xlsx":
                extract_workbook(path, snapshot)
            else:
                extract_docx(path, snapshot)
        except ExtractionError:
            raise
        except (BadZipFile, InvalidFileException, ElementTree.ParseError, OSError, KeyError, ValueError) as exc:
            raise ExtractionError("The source could not be decoded. Check the file and retry.") from exc
        classification = classify(snapshot)
        assist(snapshot, path, directory, pdf_words, classification, on_inference, use_gemini)
        snapshot["classification"] = classify(snapshot)
    snapshot.pop("headings")
    return snapshot


def validate_docx_rendering(content):
    from io import BytesIO

    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            lower = name.lower()
            if ("embeddings/" in lower or "vbaproject" in lower or "activex/" in lower
                    or lower.endswith((".svg", ".svgz", ".css", ".html", ".htm", ".mhtml"))):
                raise ExtractionError("DOCX rendering is unavailable for embedded active content.")
            if lower.endswith(".rels"):
                relationships = ElementTree.fromstring(archive.read(name))
                if any(rel.get("TargetMode", "").casefold() == "external" for rel in relationships):
                    raise ExtractionError("DOCX rendering is unavailable for files with external references.")
            if lower.startswith("word/") and lower.endswith(".xml"):
                xml = ElementTree.fromstring(archive.read(name))
                for item in xml.iter():
                    tag = item.tag.rsplit("}", 1)[-1]
                    if tag in {"altChunk", "object", "control", "attachedTemplate", "updateFields", "subDoc", "imagedata"}:
                        raise ExtractionError("DOCX rendering is unavailable for linked or active content.")
                    if tag in {"instrText", "fldSimple"}:
                        instruction = item.text or next(iter(item.attrib.values()), "")
                        if not re.match(r"\s*(?:PAGE|NUMPAGES|SECTION|SECTIONPAGES|DATE|TIME)\b", instruction, re.I):
                            raise ExtractionError("DOCX rendering is unavailable for unsupported Word fields.")


def render_docx(content):
    """Render a self-contained Word document with an isolated LibreOffice profile."""
    try:
        validate_docx_rendering(content)
    except ExtractionError:
        raise
    except (BadZipFile, ValueError, ElementTree.ParseError) as exc:
        raise ExtractionError("The DOCX contains invalid rendering data.") from exc
    with TemporaryDirectory(prefix="suriq-docx-render-") as directory:
        root = Path(directory)
        path = root / "source.docx"
        path.write_bytes(content)
        user = root / "profile" / "user"
        user.mkdir(parents=True)
        (user / "registrymodifications.xcu").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
            '</item></oor:items>'
        )
        run_command([os.getenv("SURIQ_SOFFICE", "soffice"), f"-env:UserInstallation={(root / 'profile').as_uri()}",
                     "--headless", "--norestore", "--convert-to", "pdf:writer_pdf_Export", "--outdir", directory, str(path)])
        result = root / "source.pdf"
        if not result.is_file() or result.stat().st_size > 40_000_000:
            raise ExtractionError("The DOCX paginated preview could not be created within the render limit.")
        return result.read_bytes()
