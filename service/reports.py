"""Local report exports from the verified batch snapshot."""
import csv
from io import BytesIO, StringIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def source_label(source):
    if isinstance(source["source"], int):
        return f"p. {source['source']}"
    return f"{source['source']} {', '.join(source.get('cells', []))}".strip()


def report_rows(batch):
    docs = {doc["id"]: doc["name"] for doc in batch["documents"]}
    yield ("Batch", batch["project_name"], batch["name"], "Verified", batch["verified_at"])
    yield ("Scope", batch["scope_mode"], ", ".join(batch["scope"]))
    yield ("Basis", "PHP unless declared otherwise. Tax, freight and adjustments use documented values.")
    yield ("Basis", "Metric conversions use defined factors. Packaging uses explicit evidence or a reviewer factor.")
    yield ("Basis", "Verification is for human review before purchase approval.")
    for doc in batch["documents"]:
        status = "Superseded" if doc["superseded_by"] else doc["status"]
        yield ("Document", doc["name"], doc["role"], doc["supplier"], doc["digest"], status)
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
        yield ("Supplier", supplier["supplier"], supplier["coverage"], supplier["tax_basis"],
               f"{supplier['currency']} {supplier['total']}" if supplier["total"] is not None else "Incomparable",
               "; ".join(supplier["reasons"]))
        for name, value in supplier["terms"].items():
            yield ("Term", supplier["supplier"], name, value)
        for key, item in supplier["items"].items():
            for offer in item["offers"] or [{}]:
                yield ("Material", supplier["supplier"], key, item["state"],
                       offer.get("material", offer.get("description", "")),
                       " ".join(offer.get(field, "") for field in ("quantity", "unit")).strip(),
                       " · ".join(offer[field] for field in ("unit_price", "amount") if offer.get(field)))
    for issue in batch["issues"]:
        review = issue["review"]
        yield ("Issue", issue["rule"], issue["title"], issue["severity"], issue["note"],
               review["status"], review["note"])
        for source in issue["evidence"]:
            yield ("Source", docs.get(source["document"], ""), source_label(source), source["text"],
                   "Review needed" if source.get("weak") else "")


def spreadsheet_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(
        ("\t", "\r", "\n")
    ) else value


def report(batch, extension):
    rows = list(report_rows(batch))
    if extension == "csv":
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(["Type", "Project / supplier / rule / file", "Name / coverage / location",
                         "Severity / basis / evidence", "Result / finding", "Review / reasons", "Note"])
        writer.writerows([spreadsheet_safe(value) for value in row] for row in rows)
        return output.getvalue().encode("utf-8-sig"), "text/csv"
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles["Title"].textColor = colors.HexColor("#19392e")
    story = [Paragraph("SuriQ | Verification report", styles["Title"]), Spacer(1, 14)]
    for heading, *values in rows:
        text = escape(" · ".join(str(v) for v in values if v)).replace("\n", "<br/>")
        style = styles["Heading3"] if heading in {"Issue", "Supplier"} else styles["BodyText"]
        story.append(Paragraph(f"<b>{heading}</b>  {text}", style))
    SimpleDocTemplate(output, title="SuriQ Verification Report").build(story)
    return output.getvalue(), "application/pdf"
