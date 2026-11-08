"""Material scope of a bill of quantities."""
import re

WORK_ITEM = re.compile(
    r"\b(?:supply\s*(?:and|&|/)\s*install(?:ation)?|supplied and installed|installed rate|"
    r"installation of|excavation|commissioning|testing and commissioning|dismantling|mobilization)\b", re.I,
)


def is_boq(doc):
    return doc["role"] == "boq" or (
        doc["role"] == "quote" and doc.get("classification", {}).get("document_type") == "boq"
    )


def is_bundled_work(line):
    fields = line["fields"]
    return (
        bool(WORK_ITEM.search(" ".join(fields.get(key, "") for key in ("material", "description", "specification"))))
        and not all(fields.get(key) and line["anchors"].get(key) and not line["anchors"][key].get("weak")
                    for key in ("material_quantity", "material_unit"))
    )


def material_line(line):
    fields, anchors = line["fields"].copy(), line["anchors"].copy()
    if "material_quantity" in fields:
        for field in ("quantity", "unit"):
            fields.pop(field, None)
            anchors.pop(field, None)
            if value := fields.get(f"material_{field}"):
                fields[field] = value
                if source := anchors.get(f"material_{field}"):
                    anchors[field] = source
        if not line.get("schedule"):
            # Combined work rates cannot price the material quantity.
            for field in ("unit_price", "amount"):
                fields.pop(field, None)
                anchors.pop(field, None)
    for target, source in (("unit_price", "material_rate"), ("amount", "material_extension")):
        if value := fields.get(source):
            fields[target] = value
            if source in anchors:
                anchors[target] = anchors[source]
    return {**line, "fields": fields, "anchors": anchors}
