"""Highlight verified cell evidence without round-tripping the workbook."""
from collections import defaultdict
from io import BytesIO
from xml.parsers.expat import ExpatError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.minidom import parseString
from openpyxl import Workbook
from openpyxl.packaging.relationship import get_dependents
from openpyxl.styles.stylesheet import write_stylesheet
from openpyxl.xml.constants import ARC_STYLE, CONTYPES_NS, PKG_REL_NS, REL_NS
from openpyxl.xml.functions import tostring

WORKBOOK = "xl/workbook.xml"
WORKBOOK_RELS = "xl/_rels/workbook.xml.rels"
CONTENT_TYPES = "[Content_Types].xml"
EXTENDED_SHEET_NS = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
AFTER_CONDITIONAL_FORMATTING = {
    "dataValidations", "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter",
    "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags",
    "drawing", "legacyDrawing", "legacyDrawingHF", "picture", "oleObjects", "controls",
    "webPublishItems", "tableParts", "extLst",
}


class WorkbookExportError(Exception):
    pass


def serialize_xml(xml):
    # A carriage return decoded from a character reference must survive XML line-ending normalization.
    return xml.toxml(encoding="utf-8").replace(b"\r", b"&#13;")


def add_element(parent, name, attributes=None, before=()):
    tag = f"{parent.prefix}:{name}" if parent.prefix else name
    element = parent.ownerDocument.createElementNS(parent.namespaceURI, tag)
    for key, value in (attributes or {}).items():
        element.setAttribute(key, str(value))
    sibling = next((child for child in parent.childNodes
                    if child.namespaceURI == parent.namespaceURI and child.localName in before), None)
    return parent.insertBefore(element, sibling)


def add_highlight_style(styles):
    with parseString(styles) as xml:
        root = xml.documentElement
        formats = root.getElementsByTagNameNS(root.namespaceURI, "dxfs")
        dxfs = formats[0] if formats else add_element(
            root, "dxfs", before=("tableStyles", "colors", "extLst"))
        index = len(dxfs.getElementsByTagNameNS(root.namespaceURI, "dxf"))
        dxfs.setAttribute("count", str(index + 1))
        fill = add_element(add_element(dxfs, "dxf"), "fill")
        pattern = add_element(fill, "patternFill")
        add_element(pattern, "bgColor", {"rgb": "FFFFE7A7"})
        return index, serialize_xml(xml)


def highlight_sheet(sheet, cells, style):
    # Split long, noncontiguous selections into manageable ranges for Excel.
    addresses = sorted(cells)
    ranges = [" ".join(addresses[i:i + 256]) for i in range(0, len(addresses), 256)]
    with parseString(sheet) as xml:
        root = xml.documentElement
        for namespace in (root.namespaceURI, EXTENDED_SHEET_NS):
            for rule in root.getElementsByTagNameNS(namespace, "cfRule"):
                if rule.hasAttribute("priority"):
                    rule.setAttribute("priority", str(int(rule.getAttribute("priority")) + len(ranges)))
        for priority, ref in enumerate(ranges, 1):
            formatting = add_element(root, "conditionalFormatting", {"sqref": ref},
                                     before=AFTER_CONDITIONAL_FORMATTING)
            rule = add_element(formatting, "cfRule", {"type": "expression", "dxfId": style, "priority": priority})
            add_element(rule, "formula").appendChild(xml.createTextNode("1"))
        return serialize_xml(xml)


def default_styles(archive, relations, changes):
    with parseString(archive.read(WORKBOOK_RELS)) as xml:
        ids = relations.to_dict()
        number = len(ids) + 1
        while f"rId{number}" in ids:
            number += 1
        add_element(xml.documentElement, "Relationship",
                    {"Id": f"rId{number}", "Type": f"{REL_NS}/styles", "Target": f"/{ARC_STYLE}"})
        changes[WORKBOOK_RELS] = serialize_xml(xml)
    with parseString(archive.read(CONTENT_TYPES)) as xml:
        overrides = xml.getElementsByTagNameNS(CONTYPES_NS, "Override")
        if not any(element.getAttribute("PartName") == f"/{ARC_STYLE}" for element in overrides):
            add_element(xml.documentElement, "Override", {
                "PartName": f"/{ARC_STYLE}",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
            })
            changes[CONTENT_TYPES] = serialize_xml(xml)
    return archive.read(ARC_STYLE) if ARC_STYLE in archive.namelist() else tostring(write_stylesheet(Workbook()))


def annotated_workbook(original, issues, document):
    cells_by_sheet = defaultdict(set)
    for issue in issues:
        if issue["review"]["status"] == "false_positive":
            continue
        for anchor in issue["evidence"]:
            if anchor["document"] == document and anchor.get("cells") and anchor.get("scope") != "document":
                cells_by_sheet[anchor["source"]].update(anchor["cells"])
    if not cells_by_sheet:
        return original

    changes = {}
    output = BytesIO()
    try:
        with ZipFile(BytesIO(original)) as archive:
            relations = get_dependents(archive, WORKBOOK_RELS)
            paths = {rel.Id: rel.target for rel in relations.find(f"{REL_NS}/worksheet")
                     if rel.TargetMode != "External"}
            with parseString(archive.read(WORKBOOK)) as xml:
                root = xml.documentElement
                sheets = {node.getAttribute("name"): paths[node.getAttributeNS(REL_NS, "id")]
                          for node in root.getElementsByTagNameNS(root.namespaceURI, "sheet")
                          if node.getAttribute("name") in cells_by_sheet}
            if sheets.keys() != cells_by_sheet.keys():
                raise ValueError("A finding refers to a worksheet outside the workbook.")

            styles_path = next((rel.target for rel in relations.find(f"{REL_NS}/styles")
                                if rel.TargetMode != "External"), None)
            styles = archive.read(styles_path) if styles_path else default_styles(archive, relations, changes)
            style, changes[styles_path or ARC_STYLE] = add_highlight_style(styles)
            for sheet, path in sheets.items():
                changes[path] = highlight_sheet(archive.read(path), cells_by_sheet[sheet], style)
            # Preserve every package part except the XML that owns the highlight rules.
            with ZipFile(output, "w") as exported:
                exported.comment = archive.comment
                for entry in archive.infolist():
                    data = changes.pop(entry.filename) if entry.filename in changes else archive.read(entry)
                    exported.writestr(entry, data)
                for path, data in changes.items():
                    exported.writestr(path, data)
    except (BadZipFile, DefusedXmlException, ExpatError, KeyError, ValueError) as exc:
        raise WorkbookExportError("The workbook could not be annotated. Download the original workbook.") from exc
    return output.getvalue()
