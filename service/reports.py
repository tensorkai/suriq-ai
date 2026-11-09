"""Local report exports from the verified batch snapshot."""
import csv
from collections import defaultdict
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase.pdfmetrics import registerFont, registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import CondPageBreak, HRFlowable, LongTable, Paragraph, SimpleDocTemplate, Spacer, Table

from service.boq import is_boq, material_line
from service.presentation import REVIEW, material_label, money_display, source_label, supplier_label

REPORT_FONT = "SuriQ Sans"
REPORT_BOLD = "SuriQ Sans Bold"
registerFont(TTFont(REPORT_FONT, "Vera.ttf"))
registerFont(TTFont(REPORT_BOLD, "VeraBd.ttf"))
registerFontFamily(REPORT_FONT, normal=REPORT_FONT, bold=REPORT_BOLD)


def report_rows(batch):
    docs = {doc["id"]: doc["name"] for doc in batch["documents"]}
    yield ("Batch", batch["project_name"], batch["name"], "Verified", batch["verified_at"])
    yield ("Scope", batch["scope_mode"], ", ".join(batch["scope"]))
    yield ("Basis", "PHP unless declared otherwise. Tax, freight and adjustments use documented values.")
    yield ("Basis", "Metric conversions use defined factors. Packaging uses explicit evidence or a reviewer factor.")
    yield ("Basis", "Verification is for human review before purchase approval.")
    for supplier in batch["comparison"]:
        name = supplier_label(supplier["supplier"], docs.get(supplier["document"], "quotation"))
        yield ("Supplier", name, supplier["coverage"], supplier["tax_basis"],
               f"{supplier['currency']} {supplier['total']}" if supplier["total"] is not None else "Incomparable",
               "; ".join(supplier["reasons"]))
    summary = batch.get("summary", {})
    for offer in summary.get("offers", []):
        for field in summary["fields"]:
            cell = offer["cells"][field["key"]]
            if cell["value"] or cell["reasons"]:
                yield ("Summary", offer["supplier"], field["label"], "", cell["value"],
                       "\n".join(cell["reasons"]), "\n".join(dict.fromkeys(
                           f"{docs.get(source['document'], '')} {source_label(source)}"
                           for source in cell["evidence"])))
    for issue in batch["issues"]:
        review = issue["review"]
        yield ("Issue", issue["rule"], issue["title"], issue["severity"], issue["note"],
               review["status"], review["note"])
        for source in issue["evidence"]:
            yield ("Source", docs.get(source["document"], ""), source_label(source), source["text"],
                   "Review needed" if source.get("weak") else "")
    for doc in batch["documents"]:
        status = "Superseded" if doc["superseded_by"] else doc["status"]
        supplier = supplier_label(doc["supplier"], doc["name"]) if doc["role"] == "quote" else doc["supplier"]
        yield ("Document", doc["name"], doc["role"], supplier, doc["digest"], status)
        if doc["revises"]:
            yield ("Revision", doc["name"], docs.get(doc["revises"], "Previous revision"))
        for line in doc["lines"]:
            if line.get("factor") and not line.get("excluded") and not doc["superseded_by"]:
                yield ("Conversion", doc["name"], line["fields"].get("code", line["id"]),
                       f"{line['factor']} BOM units per quoted unit")
    for entry in batch["history"]:
        data = entry["data"]
        if entry["action"] in {"correct_line", "correct_terms", "add_line", "correct_role"}:
            after = data.get("after", {})
            if isinstance(after, dict):
                changes = ", ".join(f"{field} = {value}" for field, value in after.get("values", {}).items())
                if "excluded" in after:
                    changes = f"{changes} · Row {'excluded' if after['excluded'] else 'restored'}".strip(" ·")
            else:
                changes = f"Type set to {after}"
            yield ("Correction", docs.get(data["document"], ""), data.get("target", ""), entry["created"],
                   changes, data.get("note") or data.get("reason", ""))
    for supplier in batch["comparison"]:
        name = supplier_label(supplier["supplier"], docs.get(supplier["document"], "quotation"))
        for term, value in supplier["terms"].items():
            yield ("Term", name, term, value)
        for key, item in supplier["items"].items():
            for offer in item["offers"] or [{}]:
                yield ("Material", name, key, item["state"],
                       offer.get("material", offer.get("description", "")),
                       " ".join(offer.get(field, "") for field in ("quantity", "unit")).strip(),
                       " · ".join(offer[field] for field in ("unit_price", "amount") if offer.get(field)))


def spreadsheet_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\r", "\n")
    ) else value


def report(batch, extension):
    if extension == "csv":
        output = StringIO()
        writer = csv.writer(output)
        header = ("Type", "Project / supplier / rule / file", "Name / coverage / location",
                  "Severity / basis / evidence", "Result / finding", "Review / reasons", "Note")
        writer.writerow(header)
        for row in report_rows(batch):
            writer.writerow(spreadsheet_safe(value) for value in row + ("",) * (len(header) - len(row)))
        return output.getvalue().encode("utf-8-sig"), "text/csv"
    return verification_pdf(batch), "application/pdf"


# Same tones as the UI .pill classes.
PILL_TONES = {
    **dict.fromkeys(("blocking", "failed", "mismatch"), ("#8f302d", "#fbedeb")),
    **dict.fromkeys(("warning", "needs review", "incomparable", "needs clarification", "coverage unknown",
                     "check source"), ("#744810", "#fbf2db")),
    **dict.fromkeys(("confirmed", "pass", "ready", "comparable"), ("#225639", "#e4f2e7")),
}


def pill(value):
    ink, fill = PILL_TONES.get(str(value).replace("_", " ").lower(), ("#365749", "#eaf1eb"))
    return f'<font color="{ink}" backColor="{fill}"><b>&nbsp;{report_text(str(value).replace("_", " ").capitalize())}&nbsp;</b></font>'


def report_text(value):
    # The bundled font does not include a peso sign.
    return escape(str(value).replace("₱", "PHP ").replace("→", "to")).replace("\n", "<br/>")


def verification_pdf(batch):
    output = BytesIO()
    report = SimpleDocTemplate(output, title="SuriQ Verification Report", pagesize=landscape(A4),
                               leftMargin=34, rightMargin=34, topMargin=36, bottomMargin=37)
    styles = getSampleStyleSheet()
    ink, green, muted, rule = map(colors.HexColor, ("#1b2d25", "#235741", "#52665b", "#d5ded7"))
    for name in ("Heading1", "Heading2", "Heading3"):
        styles[name].fontName = REPORT_BOLD
    styles["Heading1"].textColor = green
    styles["Heading1"].spaceBefore = 14
    styles["Heading2"].textColor = ink
    styles["Heading3"].textColor = green
    styles.add(ParagraphStyle("ReportLine", fontName=REPORT_FONT, fontSize=9.4, leading=12.3,
                              spaceBefore=2, spaceAfter=2, textColor=ink))
    styles.add(ParagraphStyle("ReportCell", fontName=REPORT_FONT, fontSize=8.4, leading=11.5, textColor=ink))
    styles.add(ParagraphStyle("ReportAmount", parent=styles["ReportCell"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle("ReportHeader", parent=styles["ReportCell"], fontName=REPORT_BOLD,
                              textColor=green))
    styles.add(ParagraphStyle("ReportAmountHeader", parent=styles["ReportHeader"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle("ReportLabel", parent=styles["ReportCell"], fontName=REPORT_BOLD, fontSize=7.4,
                              leading=10, textColor=muted, spaceAfter=1))
    styles.add(ParagraphStyle("ReportValue", parent=styles["ReportCell"], fontName=REPORT_BOLD))
    styles.add(ParagraphStyle("ReportMuted", parent=styles["ReportCell"], fontSize=7.6, leading=10.2,
                              textColor=muted, spaceBefore=2))
    styles.add(ParagraphStyle("ReportPills", parent=styles["ReportCell"], leading=14, spaceAfter=2, keepWithNext=True))
    styles.add(ParagraphStyle("ReportKicker", parent=styles["ReportHeader"], fontSize=9,
                              spaceAfter=5, textColor=muted, keepWithNext=True))
    docs = {doc["id"]: doc for doc in batch["documents"]}
    issues = {issue["id"]: issue for issue in batch["issues"]}
    story = []

    def heading(label, level=1):
        story.append(Paragraph(report_text(label), styles[f"Heading{level}"]))

    def line(value, label=""):
        prefix = f"<b>{report_text(label)}</b> " if label else ""
        story.append(Paragraph(f"{prefix}{report_text(value)}", styles["ReportLine"]))

    def table(header, rows, fractions, amounts=()):
        rich = any(isinstance(value, list) for row in [header, *rows] for value in row)
        def cell(value, style):
            return value if isinstance(value, list) else Paragraph(report_text(value or "—"), styles[style])
        body = [[cell(value, "ReportAmountHeader" if index in amounts else "ReportHeader")
                 for index, value in enumerate(header)]]
        body.extend([cell(value, "ReportAmount" if index in amounts else "ReportCell")
                     for index, value in enumerate(row)] for row in rows)
        story.append(LongTable(body, colWidths=[(report.width - 12) * share for share in fractions], repeatRows=1,
                               splitInRow=0 if rich else 1, hAlign="LEFT", style=[
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f1eb")),
                                   ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9f6")]),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                                   ("TOPPADDING", (0, 0), (-1, -1), 6),
                                   ("LINEBELOW", (0, 0), (-1, 0), .7, rule),
                                   ("LINEBELOW", (0, 1), (-1, -1), .4, rule),
                               ]))

    def facts(rows):
        """Labelled boxes like the UI's finding values; a 1-item row spans the full width."""
        width = report.width - 12
        body, spans = [], []
        for index, row in enumerate(rows):
            cells = [[Paragraph(report_text(label), styles["ReportLabel"]),
                      Paragraph(report_text(value or "Unestablished"), styles["ReportCell"])] for label, value in row]
            if len(cells) == 1:
                cells.append("")
                spans.append(("SPAN", (0, index), (1, index)))
            body.append(cells)
        story.append(Table(body, colWidths=[width / 2] * 2, hAlign="LEFT", style=[
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), .6, rule),
            ("INNERGRID", (0, 0), (-1, -1), .4, rule),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbfcfa")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            *spans,
        ]))

    def separator():
        story.append(HRFlowable(width="100%", thickness=.6, color=rule, spaceBefore=12, spaceAfter=10))

    def groups(items, size=3):
        return [items[start:start + size] for start in range(0, len(items), size)]

    def sources(anchors):
        locations = defaultdict(list)
        for anchor in anchors:
            key = (anchor["document"], anchor["source"], anchor.get("scope"))
            locations[key].extend(anchor.get("cells", []))
        return [f"{docs.get(document, {}).get('name', 'Source')} · "
                + source_label({"source": location, "scope": scope, "cells": list(dict.fromkeys(cells))})
                for (document, location, scope), cells in locations.items()]

    def summary_notes(cell, field):
        findings = [" · ".join(filter(None, (issues[key]["title"], issues[key]["detail"]["context"]
                                             if field in {"findings", "confirmed_blockers"} else "")))
                    for key in cell["findings"][:3] if key in issues]
        if len(cell["findings"]) > 3:
            findings.append(f"{len(cell['findings']) - 3} more in Findings")
        return "\n".join(dict.fromkeys(note for note in [*cell["reasons"], *findings]
                                       if note and note != cell["value"]))

    summary = batch["summary"]
    offers = summary["offers"]
    story.append(Paragraph("SuriQ verification report", styles["ReportKicker"]))
    heading("Supplier Quotation Summary")
    header = [[("Project and batch", " · ".join(filter(None, (summary["project"], batch["name"], summary["site"])))),
               ("Purchase scope", summary["scope"])],
              [("Purpose", "Internal canvass"), ("Verified", summary["verified"])]]
    if count := summary["exclusions"]:
        header.append([("Exclusions", f"{count} baseline {'row is' if count == 1 else 'rows are'} "
                                      "outside the material comparison.")])
    facts(header)
    story.append(Spacer(1, 10))
    table(["Supplier", "Quote reference", "Quoted offer total", "Comparable payable total"], [
        [offer["supplier"], offer["cells"]["reference"]["value"], offer["cells"]["quoted_total"]["value"],
         offer["cells"]["payable"]["value"]] for offer in offers
    ], [.36, .24, .19, .21], amounts=(2, 3))

    story.append(CondPageBreak(170))
    heading("Findings")
    if not issues:
        line("No findings in this verification.")
    for index, issue in enumerate(issues.values(), 1):
        detail = issue["detail"]
        story.append(CondPageBreak(160))
        if index > 1:
            separator()
        story.append(Paragraph(" ".join(pill(value) for value in (
            issue["severity"], REVIEW[issue["review"]["status"]],
            "Check source" if issue.get("review_required") else "") if value), styles["ReportPills"]))
        heading(f"{index:02d} · {issue['title']}", 2)
        if detail["context"]:
            story.append(Paragraph(report_text(detail["context"]), styles["ReportKicker"]))
        rows = [[("Expected or required", detail["expected"]), ("Offered or observed", detail["observed"])],
                [("Difference or unresolved reason", detail["reason"])], [("Action", detail["action"])]]
        if issue["review"]["note"]:
            rows.append([("Internal note", issue["review"]["note"])])
        facts(rows)
        if issue["evidence"]:
            story.append(CondPageBreak(82))
            heading(f"Source evidence · finding {index:02d}", 3)
            table(["Role", "Source and location", "Evidence excerpt"], [
                [detail["source_labels"].get(anchor["document"], "Source"),
                 f"{docs.get(anchor['document'], {}).get('name', 'Source')} · {source_label(anchor)}",
                 anchor["text"]] for anchor in issue["evidence"]
            ], [.12, .31, .57])

    story.append(CondPageBreak(102))
    heading("Scope and sources")
    facts([[("Purchase scope", summary["scope"])]])
    story.append(Spacer(1, 8))
    role_labels = {"bom": "Bill of Materials", "boq": "Bill of Quantities", "quote": "Supplier quotation",
                   "buyer": "Buyer requirements", "bundle": "Quotation bundle", "reference": "Reference"}
    def page_assignments(doc):
        if doc.get("bundle_pages"):
            return "Original pages " + ", ".join(map(str, doc["bundle_pages"]))
        bundle = doc.get("bundle") or {}
        parts = []
        if bundle.get("reference_pages"):
            parts.append("Reference pages " + ", ".join(map(str, bundle["reference_pages"])))
        if bundle.get("excluded"):
            parts.extend(f"Excluded p. {entry['page']} · {entry['reason']}" for entry in bundle["excluded"])
        return "\n".join(parts)

    table(["Document", "Role", "Status", "Page assignments"], [
        [doc["name"], role_labels.get(doc["role"], doc["role"]),
         "Superseded" if doc["superseded_by"] else "Historical split" if doc.get("bundle_active") is False
         else "Selected baseline" if doc["id"] == batch.get("baseline_document")
         else doc["status"].replace("_", " ").capitalize(),
         page_assignments(doc)]
        for doc in docs.values()
    ], [.39, .19, .18, .24])

    story.append(CondPageBreak(132))
    heading("Commercial terms and evidence")
    def summary_cell(cell, key):
        parts = [Paragraph(report_text(cell["value"] or "Unstated"), styles["ReportValue"])]
        parts += [Paragraph("• " + report_text(note), styles["ReportCell"])
                  for note in summary_notes(cell, key).split("\n") if note]
        parts += [Paragraph(report_text(source), styles["ReportMuted"]) for source in sources(cell["evidence"])]
        return parts

    for group in groups(offers):
        story.append(CondPageBreak(96))
        share = .78 / len(group)
        table(["Comparison basis", *[[Paragraph(report_text(offer["supplier"]), styles["ReportHeader"]),
                                      Paragraph(pill("Comparable" if offer.get("comparable") else "Incomparable"),
                                                styles["ReportCell"])] for offer in group]],
              [[field["label"], *[summary_cell(offer["cells"][field["key"]], field["key"]) for offer in group]]
               for field in summary["fields"]], [.22, *[share] * len(group)])
        story.append(Spacer(1, 8))

    comparisons = {row["document"]: row for row in batch["comparison"]}
    baseline = docs.get(batch.get("baseline_document")) or next(
        (doc for doc in docs.values() if doc["role"] in {"bom", "boq"}), None)
    required = {}
    if baseline:
        boq = is_boq(baseline)
        for item in baseline["lines"]:
            fields = material_line(item)["fields"] if boq else item["fields"]
            quantity = " ".join(filter(None, (fields.get("quantity"), fields.get("unit"))))
            required[item["id"]] = "\n".join(filter(None, (material_label(fields), quantity)))
    itemized = [offer for offer in offers if not docs[offer["document"]].get("summary_only")]
    if itemized:
        story.append(CondPageBreak(102))
        heading("Detailed line items")

    def item_cell(item, currency):
        if not item:
            return [Paragraph(pill("Unknown"), styles["ReportCell"])]
        parts = [Paragraph(pill(item["state"]), styles["ReportCell"])]
        for fields in item["offers"]:
            parts.append(Paragraph(report_text(" · ".join(filter(None, (
                " ".join(filter(None, (fields.get("quantity"), fields.get("unit")))) or "—",
                money_display(fields.get("unit_price"), currency),
                fields.get("amount") and "Line " + money_display(fields.get("amount"), currency))))),
                styles["ReportValue"]))
            parts.append(Paragraph(report_text(" · ".join(dict.fromkeys(filter(None, (
                material_label(fields), fields.get("specification"), fields.get("pack"), fields.get("grade")))))),
                styles["ReportMuted"]))
        return parts

    for group in groups(itemized):
        rows = [comparisons[offer["document"]] for offer in group]
        keys = list(dict.fromkeys(key for row in rows for key in row["items"]))
        story.append(CondPageBreak(82))
        if not keys:
            line("No line item results are available.")
            continue
        share = .74 / len(group)
        table(["Required material", *[offer["supplier"] for offer in group]],
              [[required.get(key) or key, *[item_cell(row["items"].get(key), row["currency"]) for row in rows]]
               for key in keys], [.26, *[share] * len(group)])
        story.append(Spacer(1, 8))

    corrections = [row for row in report_rows(batch) if row[0] in {"Conversion", "Correction"}]
    if corrections:
        story.append(CondPageBreak(100))
        heading("Corrections and conversions")
        table(["Record", "Document and target", "Details"], [
            [kind, "\n".join(str(value) for value in (document, target) if value),
             "\n".join(str(value) for value in values if value)]
            for kind, document, target, *values in corrections
        ], [.15, .30, .55])

    def page_number(canvas, doc):
        canvas.saveState()
        left, right = doc.leftMargin + 6, doc.pagesize[0] - doc.rightMargin - 6
        canvas.setStrokeColor(rule)
        canvas.line(left, 30, right, 30)
        canvas.setFillColor(muted)
        canvas.setFont(REPORT_FONT, 8)
        canvas.drawString(left, 17, "SuriQ · Internal canvass")
        canvas.drawRightString(right, 17, f"Page {doc.page}")
        canvas.restoreState()

    report.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return output.getvalue()
