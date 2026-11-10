"""Deterministic issue grouping, scope checks and decimal commercial comparisons."""
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext

from service.extraction import ATTRIBUTES
from service.commercial import (
    COMMERCIAL_TERMS, COST_LABELS, MONEY_TERMS, as_manila, charge_treatment, compare_term,
    currency_code, date_reading, document_currency, monetary_value, term_identity, timing_reading,
    validity_interpretation, vat_treatment,
)

PRECISION = Decimal(1).scaleb(-int(os.getenv("SURIQ_MONEY_DECIMALS", "2")))
UNIT_ALIASES = {
    "pc": "pcs", "piece": "pcs", "pieces": "pcs", "bag": "bags", "sack": "sacks",
    "length": "lengths", "set": "sets", "roll": "rolls", "pack": "packs", "pail": "pails",
    "m2": "sqm", "m²": "sqm", "sqm": "sqm", "m3": "cum", "m³": "cum", "cum": "cum",
    "tonne": "mt", "tonnes": "mt", "liter": "l", "liters": "l",
}
METRIC = {
    "g": ("mass", Decimal(".001")), "kg": ("mass", Decimal(1)), "mt": ("mass", Decimal(1000)),
    "mm": ("length", Decimal(".001")), "cm": ("length", Decimal(".01")), "m": ("length", Decimal(1)),
}
PACK_UNITS = {"bags", "sacks", "lengths", "sets", "rolls", "packs", "pails"}
# Units SuriQ can read; anything else is flagged on the BOM so nobody assumes a conversion.
KNOWN_UNITS = PACK_UNITS | {"pcs", "sqm", "cum", "l", "lot", "lots", "unit", "units", "ea", "each", "box", "boxes",
                            "gal", "gallon", "gallons", "ft", "in", "inch", "sheet", "sheets", "pair", "pairs",
                            "can", "cans", "tube", "tubes", "bd ft", "bdft", "lm", "ln m", "km", "t", "assy"}
RULES = {
    "dimension": "Dimension mismatch", "grade": "Grade requirement", "standard": "Standard requirement",
    "brand": "Required brand", "specification": "Specification mismatch", "material": "Material mismatch",
    "quantity": "Quantity mismatch", "unit": "Unit clarification", "pack": "Pack size mismatch",
    "missing": "Item not quoted", "extra": "Additional item", "match": "Material match needs review",
    "duplicate": "Duplicate offer lines", "line_math": "Line arithmetic", "subtotal": "Subtotal arithmetic",
    "total": "Payable total arithmetic", "tax": "VAT basis unclear", "charges": "Charges unclear",
    "validity": "Quotation validity", "delivery": "Delivery requirement", "buyer_terms": "Buyer requirement",
    "extraction": "Source needs review", "terms": "Conflicting quote terms",
    "supplier_duplicate": "Multiple offers from supplier", "outside_scope": "Item outside purchase scope",
    "materials_summary": "Stated materials total difference",
    "materials_reconciliation": "Materials reconciliation needs review",
    "materials_price_variance": "Materials price variance",
    "work_scope": "BOQ work item needs material breakdown",
    "baseline": "BOM line needs clarification",
}


def norm(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def compact(value):
    return re.sub(r"[\s.]", "", value.casefold())


def decimal_value(value):
    text = re.sub(r"^(?:PHP\.?|₱)\s*", "", value.strip(), flags=re.I)
    if not re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d{1,15})(?:\.\d{1,8})?", text):
        return None
    if len(text.replace(",", "")) > 22:
        return None
    return Decimal(text.replace(",", ""))


def decimal_text(value):
    return format(value.normalize(), "f")


def money(value):
    with localcontext() as context:
        context.prec = 60
        return format(value.quantize(PRECISION, rounding=ROUND_HALF_UP), "f")


def unit(value):
    key = compact(value)
    return UNIT_ALIASES.get(key, key)


def identity(line):
    fields = line["fields"]
    value = fields.get("material") or fields.get("description", "")
    for pattern in ATTRIBUTES.values():
        value = re.sub(pattern, "", value, flags=re.I)
    return norm(value)


def attribute_value(field, value):
    value = value.casefold().strip()
    if field == "grade":
        value = re.sub(r"^(?:grade|gr\.?)\s*", "", value)
    if field == "dimension":
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mm|cm|in(?:ch(?:es)?)?)", value)
        if match:
            factor = {"mm": Decimal(1), "cm": Decimal(10)}.get(match[2], Decimal("25.4"))
            return f"{decimal_text(Decimal(match[1]) * factor)}mm"
    return compact(value)


def evidence(doc, line=None, field=None):
    if line is None:
        anchor = doc["term_anchors"].get(field)
        return {**anchor, "document": doc["id"]} if anchor else None
    anchor = line["anchors"].get(field) if field else None
    if not anchor and field is None:
        values = list(line["anchors"].values())
        if values:
            values = [value for value in values if value["source"] == values[0]["source"]]
            anchor = {**values[0], "text": " | ".join(dict.fromkeys(v["text"] for v in values)),
                      "weak": any(v.get("weak") for v in values)}
            for kind in ("boxes", "cells"):
                if kind in anchor:
                    unique = {str(item): item for value in values for item in value.get(kind, [])}
                    anchor[kind] = list(unique.values())
    return {**anchor, "document": doc["id"]} if anchor else None


def source_evidence(doc):
    return [{"document": doc["id"], "source": source["ref"], "text": source["warning"] or "Source overview",
             "weak": True} for source in doc["sources"][:1]]


def date_value(value):
    reading = date_reading(value)
    return reading.date() if isinstance(reading, datetime) else reading


def pack_size(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kg|g|l)(?:\s*(?:/|per)\s*\w+)?",
                         value.strip(), re.I)
    if not match:
        return None
    n, kind = Decimal(match[1]), match[2].lower()
    return (n * Decimal(".001"), "kg") if kind == "g" else (n, kind)


def best_matches(line, baseline):
    code = norm(line["fields"].get("code", ""))
    name = identity(line)
    sequential = bool(re.fullmatch(r"[\d ]+", code))
    # A plain row number (1, 2, 3.1) is a position, not a material identity: the names must agree too.
    by_code = [item["id"] for item in baseline if code and norm(item["fields"].get("code", "")) == code
               and (not sequential or set(name.split()) & set(identity(item).split()))]
    if by_code:
        return by_code, "item ID"
    by_name = [item["id"] for item in baseline if name and identity(item) == name]
    if len(by_name) > 1:
        specs = {field: attribute_value(field, line["fields"][field]) for field in ATTRIBUTES
                 if line["fields"].get(field)}
        narrowed = [item["id"] for item in baseline if item["id"] in by_name and specs and all(
            attribute_value(field, item["fields"].get(field, "")) == value for field, value in specs.items()
        )]
        if len(narrowed) == 1:
            return narrowed, "name and specifications"
    if by_name:
        return by_name, "normalized name"
    tokens = set(name.split())
    related = [item["id"] for item in baseline if tokens & set(identity(item).split())]
    return related, "candidate only"


def tax_basis(value):
    basis, _, _, _ = vat_treatment(value)
    return "non-VAT" if basis == "Declared non-VAT" else basis


def summary_quote(doc):
    if doc["role"] != "quote" or doc["lines"] or not doc.get("classification", {}).get("summary_only"):
        return False
    return any((amount := monetary_value(doc["terms"].get(field, ""))) is not None and amount[1] >= 0
               and (anchor := evidence(doc, field=field)) and (
                   not anchor.get("weak") or anchor.get("method") == "model"
                   and doc.get("provenance", {}).get("provider") in {"gemini", "meta"}
               )
               for field in ("materials_total", "total"))


def uncertain_source(doc):
    return (any(source["warning"] for source in doc["sources"])
            or any(anchor.get("weak") for field, anchor in doc["term_anchors"].items()
                   if field in MONEY_TERMS | {"tax", "tax_declaration", "currency"})
            or any(anchor.get("weak") for line in doc["lines"] for anchor in line["anchors"].values()))


def one_schedule(lines, scope_mode, scope):
    selected = lines if scope_mode == "all" else [line for line in lines if line["id"] in scope]
    return len({line.get("schedule") for line in selected}) <= 1


def schedule_baseline(lines, scope_mode, scope):
    """Match duplicate codes only within the schedule chosen for the purchase."""
    if len({line.get("schedule") for line in lines}) <= 1:
        return lines
    if scope_mode != "selected":
        return []
    schedules = {line.get("schedule") for line in lines if line["id"] in scope}
    if len(schedules) != 1:
        return []
    return [line for line in lines if line.get("schedule") in schedules]


def term_anchors(doc, field):
    values = [evidence(doc, field=field)]
    values.extend({**entry["anchor"], "document": doc["id"]} for entry in doc.get("term_mentions", {}).get(field, [])
                  if field not in doc.get("corrected_terms", []))
    return [anchor for index, anchor in enumerate(values) if anchor and anchor not in values[:index]]


def term_editor(doc, field):
    return {"document": doc["id"], "target": "terms", "field": field}


def terms_conflict(field, mentions, currency):
    identities = {term_identity(field, entry["value"], currency, entry.get("scope", "")) for entry in mentions}
    if field == "tax" and identities and all(identity[0] == "tax" for identity in identities):
        bases = {(basis, scope) for _, basis, _, scope in identities}
        rates = {rate for _, _, rate, _ in identities if rate is not None}
        return len(bases) > 1 or len(rates) > 1
    return len(identities) > 1


def document_term_reference(doc, text):
    return [{"document": doc["id"], "source": source["ref"], "text": text,
             "weak": True, "scope": "document"} for source in doc["sources"][:1]]


def commercial_requirements(quote, buyers, currency, add):
    entries = []
    terms = quote["terms"]
    for field, label in COMMERCIAL_TERMS.items():
        requirements = []
        for buyer in buyers:
            value = buyer["terms"].get(field)
            if not value:
                continue
            readings = [value, *(entry["value"] for entry in buyer.get("term_mentions", {}).get(field, [])
                                  if field not in buyer.get("corrected_terms", []))]
            for reading in dict.fromkeys(readings):
                requirements.append((buyer, reading))
        offered = terms.get(field, "")
        if not (requirements or offered):
            continue
        mentions = quote.get("term_mentions", {}).get(field, [])
        offer_conflict = field not in quote.get("corrected_terms", []) and terms_conflict(field, mentions, currency)
        offered_readings = "\n".join(dict.fromkeys(entry["value"] for entry in mentions)) if offer_conflict else offered
        timing = {}
        if field == "delivery":
            timing = {"required_timing": [timing_reading(value) for value in dict.fromkeys(value for _, value in requirements)],
                      "offered_timing": timing_reading(offered) if offered and not offer_conflict else None}
        actual_anchors = term_anchors(quote, field)
        if not requirements:
            reason = "The quotation contains conflicting readings of this term." if offer_conflict else ""
            finding = None
            if not reason and field not in COST_LABELS and field != "validity" and (not actual_anchors or actual_anchors[0].get("weak")):
                reason = (actual_anchors[0].get("quality_reason") if actual_anchors else None) or "Confirm this supplier term against the source."
                finding = add("extraction", [quote["id"], f"term:{field}"], "commercial field", reason,
                              "Warning", actual_anchors, review=True,
                              detail={"observed": offered, "reason": reason, "action": f"Link the supplier’s {label.lower()} terms."},
                              editors=[term_editor(quote, field)])
            entries.append({"field": field, "label": label, "required": "", "offered": offered_readings,
                            "status": "Needs clarification" if reason else "Supplier stated", "reason": reason,
                            "evidence": actual_anchors, "finding": finding, **timing})
            continue
        required = "\n".join(dict.fromkeys(value for _, value in requirements))
        anchors = [anchor for buyer, _ in requirements for anchor in term_anchors(buyer, field)]
        expected_anchors = [anchor for index, anchor in enumerate(anchors) if anchor not in anchors[:index]]
        intended = {term_identity(field, value, document_currency(buyer), buyer["terms"].get("project_site", ""))
                    for buyer, value in requirements}
        buyer, expected = requirements[0]
        if len(intended) != 1:
            status, reason = "Needs clarification", "Buyer sources state conflicting requirements. Confirm which requirement applies."
        elif offer_conflict:
            status, reason = "Needs clarification", "The quotation states conflicting values. Confirm which supplier term applies."
        elif any(not evidence(source, field=field) or evidence(source, field=field).get("weak")
                 for source, _ in requirements) or offered and (not actual_anchors or actual_anchors[0].get("weak")):
            status, reason = "Needs clarification", "Confirm the reading and link the requirement or offer to its source."
        elif (buyer_site := buyer["terms"].get("project_site")) and (offer_site := terms.get("project_site")) and norm(buyer_site) != norm(offer_site):
            status, reason = "Needs clarification", "The buyer and supplier sources name different project sites."
        else:
            status, reason = compare_term(field, expected, offered, document_currency(buyer), currency)
        if not actual_anchors:
            actual_anchors = document_term_reference(quote, f"No {label.lower()} term was established in this quote.")
        anchors = [*expected_anchors, *actual_anchors]
        finding = None
        if status != "Meets":
            condition = [required, offered_readings or "unspecified"] if field == "delivery" else [field, required, offered_readings or "unspecified"]
            action = ("Resolve the conflicting buyer requirements." if len(intended) != 1
                      else f"Confirm the supplier’s {label.lower()} terms.")
            finding = add("delivery" if field == "delivery" else "buyer_terms",
                          "batch" if field == "delivery" else quote["id"], condition, reason,
                          "Blocking" if field == "delivery" and status == "Differs" else "Warning", anchors,
                          review=status == "Needs clarification",
                          detail={"expected": required, "observed": offered_readings or "Unstated", "reason": reason, "action": action},
                          editors=([term_editor(source, field) for source, _ in requirements] if len(intended) != 1 else [])
                          + [term_editor(quote, field)])
        entries.append({"field": field, "label": label, "required": required, "offered": offered_readings,
                        "status": status, "reason": reason, "evidence": anchors, "finding": finding, **timing})
    return entries


MATERIALS_AMOUNTS = ("item_extensions_total", "materials_adjustment", "materials_total", "scoped_materials_total")


def grounded_terms(doc, field):
    if field in doc.get("corrected_terms", []) or not doc.get("term_mentions", {}).get(field):
        readings = [{"value": doc["terms"].get(field, ""), "anchor": doc["term_anchors"].get(field)}]
    else:
        readings = doc["term_mentions"][field]
    return [(entry["value"], {**anchor, "document": doc["id"]}) for entry in readings
            if entry["value"] and (anchor := entry.get("anchor")) and not anchor.get("weak")
            and (anchor.get("boxes") or anchor.get("cells"))]


def materials_currency(doc):
    currencies = {currency_code(value) for value, _ in grounded_terms(doc, "currency")}
    currencies.update(amount[0] for field in MATERIALS_AMOUNTS
                      for value, _ in grounded_terms(doc, field) if (amount := monetary_value(value)) and amount[0])
    return next(iter(currencies)) if len(currencies) == 1 and all(re.fullmatch(r"[A-Z]{3}", value) for value in currencies) else None


def printed_amount(value):
    reading = monetary_value(re.sub(r"(?<!\d)\+(?=\d)", "", value))
    return reading[1] if reading else None


def one_grounded_value(doc, field, normalize=norm):
    values = {normalize(value): (value, anchor) for value, anchor in grounded_terms(doc, field)}
    return next(iter(values.values())) if len(values) == 1 and None not in values else ("", None)


def selected_material_scope(baseline, scope, scope_name):
    rows = [line for line in baseline["lines"] if line["id"] in scope]
    listed, listed_anchor = one_grounded_value(baseline, "materials_scope_rows")
    if listed_anchor:
        codes = {compact(line["fields"].get("code", "")) for line in rows}
        return listed_anchor if "" not in codes and codes == {compact(code) for code in re.split(r"[,;|]", listed)} else None
    if len(rows) == len(baseline["lines"]) and not baseline.get("has_excluded_rows"):
        return baseline["term_anchors"].get("materials_scope")
    schedule = {line["id"] for line in baseline["lines"] if norm(line.get("schedule", "")) == norm(scope_name)}
    return baseline["term_anchors"].get("materials_scope") if schedule and schedule == set(scope) else None


def documented_material_scope(baseline, quote, mode, scope, partial):
    buyer_site, _ = one_grounded_value(baseline, "project_site")
    quote_site, _ = one_grounded_value(quote, "project_site")
    if buyer_site and quote_site and norm(buyer_site) != norm(quote_site):
        return [], "The baseline and quotation identify different project sites."
    if any(len({norm(value) for value, _ in grounded_terms(doc, "project_site")}) > 1 for doc in (baseline, quote)):
        return [], "The source lists conflicting project sites."
    if any(len({norm(value) for value, _ in grounded_terms(doc, "materials_scope")}) > 1 for doc in (baseline, quote)):
        return [], "The source lists conflicting materials scopes."

    buyer_scope, buyer_scope_anchor = one_grounded_value(baseline, "materials_scope")
    quote_scope, quote_scope_anchor = one_grounded_value(quote, "materials_scope")
    same_named_scope = bool(buyer_scope and quote_scope and norm(buyer_scope) == norm(quote_scope))
    if partial:
        mapping = selected_material_scope(baseline, scope, buyer_scope) if mode == "selected" and same_named_scope else None
        if not mapping:
            return [], "The selected or partial purchase needs explicitly equivalent materials scope totals."
        return [buyer_scope_anchor, quote_scope_anchor, {**mapping, "document": baseline["id"]}], ""
    if buyer_scope and quote_scope and not same_named_scope:
        return [], "The documents identify different materials scopes."
    buyer_ref, buyer_ref_anchor = one_grounded_value(baseline, "reference", compact)
    quote_ref, quote_ref_anchor = one_grounded_value(quote, "scope_reference", compact)
    if quote["terms"].get("scope_reference") and (not buyer_ref or not quote_ref or compact(buyer_ref) != compact(quote_ref)):
        return [], "The quotation references a different or unverified materials baseline."
    if buyer_ref and quote_ref:
        return [buyer_ref_anchor, quote_ref_anchor], ""
    if same_named_scope and buyer_site and quote_site:
        return [buyer_scope_anchor, quote_scope_anchor], ""
    return [], "The quotation does not establish equivalent scope with this materials baseline."


def reconcile_materials(baseline, quote, mode, scope):
    result = {"status": "Unresolved", "arithmetic": "Unresolved", "reason": "",
              "expected": None, "offered": None, "subtotal": None, "computed": None, "adjustments": [],
              "difference": None, "currency": None, "evidence": [], "finding": None}

    def unresolved(reason):
        result["reason"] = reason
        return result

    partial = mode == "selected" or quote["coverage"] == "partial" or baseline.get("has_excluded_rows")
    field = "scoped_materials_total" if partial else "materials_total"
    expected_raw, expected_source = one_grounded_value(baseline, field, printed_amount)
    offered_raw, offered_source = one_grounded_value(quote, field, printed_amount)
    expected, offered = printed_amount(expected_raw), printed_amount(offered_raw)
    for key, amount, source in (("expected", expected, expected_source), ("offered", offered, offered_source)):
        if amount is not None and source:
            result[key] = money(amount)
            result["evidence"].append(source)

    currency = materials_currency(baseline)
    if not currency or materials_currency(quote) != currency:
        return unresolved("A common, documented materials currency is unavailable.")
    result["currency"] = currency
    scope_evidence, reason = documented_material_scope(baseline, quote, mode, scope, partial)
    if reason:
        return unresolved(reason)
    result["evidence"].extend(anchor for anchor in scope_evidence if anchor not in result["evidence"])
    if expected is None or offered is None or expected < 0 or offered < 0:
        return unresolved("Both sources need an unambiguous, labeled materials total.")

    if not partial:
        subtotal_raw, subtotal_source = one_grounded_value(baseline, "item_extensions_total", printed_amount)
        subtotal = printed_amount(subtotal_raw)
        if subtotal_source is None and baseline["terms"].get("item_extensions_total"):
            return unresolved("The printed item-extension subtotal needs a reliable reading.")
        if subtotal is not None:
            result["subtotal"] = money(subtotal)
            result["evidence"].append(subtotal_source)
            adjustments = {}
            for raw, source in grounded_terms(baseline, "materials_adjustment"):
                adjustment = printed_amount(raw)
                label = source.get("label", "Materials reconciliation allowance")
                category = "deduction" if "deduction" in norm(label) else "surcharge" if "surcharge" in norm(label) else "allowance"
                if adjustment is not None:
                    adjustment = -abs(adjustment) if category == "deduction" else adjustment
                    if category in adjustments and adjustments[category][1] != adjustment:
                        return unresolved("The baseline shows conflicting materials adjustments.")
                    adjustments[category] = (label, adjustment, source)
            for label, adjustment, source in adjustments.values():
                result["adjustments"].append({"label": label, "value": money(adjustment)})
                result["evidence"].append(source)
            calculated = subtotal + sum((entry[1] for entry in adjustments.values()), Decimal(0))
            result["computed"] = money(calculated)
            if money(calculated) != money(expected):
                result["finding"] = "materials_reconciliation"
                return unresolved("The item-extension subtotal and documented materials adjustments do not reconcile to the stated materials total.")
            result["arithmetic"] = "Verified"

    if result["arithmetic"] != "Verified":
        result["arithmetic"] = "Unverified"
    difference = Decimal(money(offered)) - Decimal(money(expected))
    result["difference"] = money(difference)
    if money(expected) == money(offered):
        result.update(status="Matches", reason="The stated materials totals match on the documented scope.")
    elif "estimat" in norm(expected_source.get("label", "")):
        result.update(status="Price variance", finding="materials_price_variance",
                      reason=f"The offer differs from the materials estimate by {currency} {money(abs(difference))}.")
    else:
        result.update(status="Differs", finding="materials_summary",
                      reason=f"Stated materials totals differ by {currency} {money(abs(difference))} on the documented scope.")
    return result


def verify(documents, scope_mode, scope, as_of=None):
    with localcontext() as context:
        context.prec = 60
        return verify_in_context(documents, scope_mode, scope, as_of)


def verify_in_context(documents, scope_mode, scope, as_of):
    as_of = as_manila(as_of)
    bom = next(doc for doc in documents if doc["role"] in {"bom", "boq"})
    quotes = [doc for doc in documents if doc["role"] == "quote"]
    buyers = [bom, *(doc for doc in documents if doc["role"] == "buyer")]
    baseline = {line["id"]: line for line in schedule_baseline(bom["lines"], scope_mode, scope)}
    required = {key: line for key, line in baseline.items()
                if scope_mode == "all" or (scope_mode == "selected" and key in scope)}
    issues = {}
    comparisons = []
    matches = {}
    supplier_docs = defaultdict(list)
    for quote in quotes:
        supplier_docs[norm(quote["supplier"])].append(quote)
    rank = {"Blocking": 0, "Warning": 1, "Info": 2}

    def add(rule, subject, condition, note, severity, anchors, *, review=False, detail=None, editors=()):
        key = hashlib.sha256(json.dumps([rule, subject, condition], sort_keys=True).encode()).hexdigest()[:20]
        review |= any(a is None for a in anchors)
        anchors = [a for a in anchors if a]
        exact = [a for a in anchors if a.get("boxes") or a.get("cells")]
        issue = issues.setdefault(key, {
            "id": key, "rule": rule, "title": RULES[rule], "note": note, "severity": severity,
            "evidence": [], "review_required": False, "confirmable": True,
            "condition": condition, "subject": subject,
        })
        if rank[severity] < rank[issue["severity"]]:
            issue["severity"] = severity
        issue["review_required"] |= review or not exact or any(a.get("weak") for a in anchors)
        issue["confirmable"] &= bool(exact) and not review and not any(a.get("weak") for a in anchors)
        if detail:
            issue["detail"] = detail
        for editor in editors:
            links = issue.setdefault("source_editors", [])
            if editor not in links:
                links.append(editor)
        for anchor in anchors:
            if anchor not in issue["evidence"]:
                issue["evidence"].append(anchor)
        return key

    for doc in documents:
        if doc["role"] not in {"bom", "boq", "quote", "buyer"}:
            continue
        for line in doc.get("work_lines", []):
            action = "Link separate material quantity and unit evidence or keep this work item outside the material comparison."
            add("work_scope", [doc["id"], line["id"]], "bundled work",
                "This work item has no separately documented material quantity and unit.", "Warning",
                [evidence(doc, line)], review=True,
                detail={"observed": line["fields"].get("material", line["fields"].get("description", "")), "action": action},
                editors=[{"document": doc["id"], "target": line["id"], "field": "material_quantity"}])
        for source in doc["sources"]:
            if source["warning"]:
                add("extraction", doc["id"], source["ref"], source["warning"], "Warning",
                    [{"document": doc["id"], "source": source["ref"], "text": source["warning"], "weak": True}],
                    review=True)
        for line in doc["lines"]:
            fields = line["fields"]
            if not fields.get("unit") or decimal_value(fields.get("quantity", "")) is None:
                add("extraction", [doc["id"], line["id"]], "quantity or unit",
                    "Material row has a missing or unreadable quantity or unit.", "Warning",
                    [evidence(doc, line)], review=True)
        if doc["role"] != "buyer" and not doc["lines"] and not summary_quote(doc):
            add("extraction", doc["id"], "no rows", "No material rows were recognized.", "Warning",
                source_evidence(doc), review=True)

    codes = defaultdict(list)
    for line in baseline.values():
        fields = line["fields"]
        quantity, measure = decimal_value(fields.get("quantity", "")), unit(fields.get("unit", ""))
        if quantity is not None and quantity <= 0:
            add("baseline", line["id"], "quantity", "The BOM quantity is zero or negative.", "Warning",
                [evidence(bom, line, "quantity")], review=True,
                detail={"observed": fields["quantity"], "action": "Confirm the required quantity with the requester."})
        if measure and measure not in KNOWN_UNITS and measure not in METRIC:
            add("baseline", line["id"], "unit", f"Unit \"{fields['unit']}\" is not recognized, so no conversion is applied.",
                "Info", [evidence(bom, line, "unit")], review=True,
                detail={"observed": fields["unit"], "action": "Confirm the unit or correct the BOM row."})
        if (code := norm(fields.get("code", ""))) and not re.fullmatch(r"[\d ]+", code):
            codes[code].append(line)
    for code, lines in codes.items():
        if len({identity(line) for line in lines}) > 1:
            add("baseline", code, "duplicate item ID", f"Item ID {lines[0]['fields']['code']} is used for different materials.",
                "Warning", [evidence(bom, line) for line in lines], review=True,
                detail={"observed": lines[0]["fields"]["code"], "action": "Give each BOM material its own item ID."})

    for supplier, group in supplier_docs.items():
        if len(group) > 1:
            add("supplier_duplicate", supplier, "multiple quotes", "The same supplier is assigned multiple offers.",
                "Warning", [evidence(quote, field="reference") or next(iter(source_evidence(quote)), None)
                            for quote in group], review=True)

    for quote in quotes:
        offered = defaultdict(list)
        summary = summary_quote(quote)
        uncertain = not quote["lines"] or uncertain_source(quote)
        bad_lines = False
        line_amounts = []
        for line in quote["lines"]:
            f = line["fields"]
            candidates, method = best_matches(line, list(baseline.values()))
            key = line.get("match") if "match" in line else (
                candidates[0] if len(candidates) == 1 and method != "candidate only" and
                not any(line["anchors"].get(field, {}).get("weak")
                        for field in ("code", "material", "description")) else None
            )
            if "match" in line:
                method = "manual"
            matches[f"{quote['id']}:{line['id']}"] = {"bom": key, "candidates": candidates, "method": method}
            if key in baseline:
                offered[key].append(line)
                if scope_mode == "selected" and key not in required:
                    add("outside_scope", key, "excluded from scope", "This quoted item is outside the purchase scope.",
                        "Info", [evidence(bom, baseline[key]), evidence(quote, line)])
            else:
                uncertain = True
                rule = "extra" if key == "" or not candidates else "match"
                note = ("Quoted line has no confirmed BOM match." if rule == "extra" else
                        "Confirm which BOM row this quoted item belongs to.")
                add(rule, [quote["id"], line["id"]], identity(line), note, "Warning",
                    [evidence(quote, line)], review=key != "")
            q, price, amount = [decimal_value(f.get(name, "")) for name in ("quantity", "unit_price", "amount")]
            if amount is not None:
                line_amounts.append(amount)
            numeric_evidence = [evidence(quote, line, field) for field in ("quantity", "unit_price", "amount")]
            if q is None or price is None or amount is None or min(q, price, amount) < 0 or any(
                not anchor or anchor.get("weak") for anchor in numeric_evidence
            ):
                bad_lines = True
            elif money(q * price) != money(amount):
                bad_lines = True
                add("line_math", [quote["id"], line["id"]], [money(q * price), money(amount)],
                    f"Quantity × unit price is {money(q * price)}. The line shows {money(amount)}.",
                    "Warning", numeric_evidence)

        matrix = {}
        for key, line in required.items():
            bf = line["fields"]
            item = bf.get("code") or f"Row {key}"
            matched = offered[key]
            if not matched:
                if summary:
                    matrix[key] = {"state": "Not itemized", "offers": []}
                    continue
                coverage = quote["coverage"]
                is_full = coverage == "full" and not uncertain
                note = (f"{item} is absent from the full-scope offer." if is_full else
                        f"{item} is not offered." if coverage == "partial" else
                        f"{item} has no confirmed offer. Coverage needs clarification.")
                if is_full:
                    add("missing", key, "absent from full offer", note, "Blocking",
                        [evidence(bom, line), *source_evidence(quote)], review=True)
                matrix[key] = {"state": "missing" if is_full else "not offered" if coverage == "partial" else
                               "coverage unknown", "offers": []}
                continue
            if len(matched) > 1:
                add("duplicate", key, "multiple offer lines", f"{item} has multiple lines in one offer.",
                    "Blocking", [evidence(quote, offer) for offer in matched], review=True)
                matrix[key] = {"state": "needs review", "offers": [offer["fields"] for offer in matched]}
                continue

            offer = matched[0]
            qf = offer["fields"]
            state = "pass"

            def difference(rule, expected, actual, *, review=False, bfield=None, qfield=None):
                nonlocal state
                left = evidence(bom, line, bfield or rule)
                right = evidence(quote, offer, qfield or rule)
                review |= (bool(expected) and not left) or (bool(actual) and not right)
                anchors = [left or evidence(bom, line), right or evidence(quote, offer)]
                expected = expected or "unspecified"
                actual = actual or "unspecified"
                weak = any(anchor and anchor.get("weak") for anchor in anchors)
                issue = add(rule, key, [attribute_value(rule, expected), attribute_value(rule, actual)],
                    f"{item} requires {expected[:66]}, while the offer lists {actual[:66]}.",
                    "Warning" if review or weak else "Blocking", anchors, review=review)
                if not review and not weak:
                    state = "mismatch"
                elif state == "pass":
                    state = "needs review"
                return issue

            if identity(line) and identity(offer) and identity(line) != identity(offer):
                field = "material" if bf.get("material") else "description"
                offer_field = "material" if qf.get("material") else "description"
                difference("material", bf[field], qf[offer_field], review=True, bfield=field, qfield=offer_field)
            for field in ("dimension", "grade", "standard", "brand"):
                expected, actual = bf.get(field, ""), qf.get(field, "")
                if expected and attribute_value(field, expected) != attribute_value(field, actual):
                    difference(field, expected, actual, review=not actual and field in {"dimension", "brand"})
            if bf.get("specification"):
                expected, actual = bf["specification"], qf.get("specification", "")
                for pattern in ATTRIBUTES.values():
                    expected = re.sub(pattern, "", expected, flags=re.I)
                    actual = re.sub(pattern, "", actual, flags=re.I)
                if norm(expected) and norm(expected) != norm(actual):
                    difference("specification", bf["specification"], qf.get("specification", ""), review=True)

            q1, q2 = [decimal_value(f.get("quantity", "")) for f in (bf, qf)]
            u1, u2 = [unit(f.get("unit", "")) for f in (bf, qf)]
            p1, p2 = [pack_size(f.get("pack", "")) for f in (bf, qf)]
            override = decimal_value(offer.get("factor", ""))
            repacked = u1 in PACK_UNITS and u2 in PACK_UNITS and p1 and p2 and p1 != p2
            if repacked:
                issue = difference("pack", bf["pack"], qf["pack"])
                if p1[1] == p2[1] and q1 and q2 and q1 * p1[0] == q2 * p2[0] and "Same total" not in issues[issue]["note"]:
                    issues[issue]["note"] += (f" Same total of {decimal_text(q1 * p1[0])} {p1[1]}"
                                              " in a different bag size. Approve the substitution.")
            elif not override and (u1 in PACK_UNITS or u2 in PACK_UNITS) and (
                bool(p1) != bool(p2) or (u1 in {"bags", "sacks"} and u2 in {"bags", "sacks"} and not (p1 and p2))
            ):
                difference("unit", bf.get("pack", bf.get("unit", "")), qf.get("pack", qf.get("unit", "")),
                           review=True, bfield="pack" if p1 else "unit", qfield="pack" if p2 else "unit")
            if q1 is None or q2 is None or min(q1, q2) <= 0:
                difference("quantity", bf.get("quantity", ""), qf.get("quantity", ""), review=True)
            elif override:
                if q1 != q2 * override:
                    difference("quantity", f"{decimal_text(q1)} {u1}",
                               f"{decimal_text(q2 * override)} {u1} (reviewer conversion)")
            elif repacked and p1[1] == p2[1]:
                # Different bag sizes: compare the physical total (100 x 40 kg = 80 x 50 kg), not the bag count.
                if q1 * p1[0] != q2 * p2[0]:
                    difference("quantity", f"{decimal_text(q1 * p1[0])} {p1[1]}", f"{decimal_text(q2 * p2[0])} {p2[1]}")
            elif u1 and (u1 == u2 or (u1 in {"bags", "sacks"} and u2 in {"bags", "sacks"} and p1 and p1 == p2)):
                if q1 != q2:
                    difference("quantity", f"{decimal_text(q1)} {u1}", f"{decimal_text(q2)} {u1}")
            elif u1 in METRIC and u2 in METRIC and METRIC[u1][0] == METRIC[u2][0]:
                if q1 * METRIC[u1][1] != q2 * METRIC[u2][1]:
                    difference("quantity", f"{decimal_text(q1)} {u1}", f"{decimal_text(q2)} {u2}")
            else:
                difference("unit", bf.get("unit", ""), qf.get("unit", ""), review=True)

            for field in ("delivery_date", "lead_time"):
                if not bf.get(field):
                    continue
                term = quote["terms"].get("delivery", "")
                observed = qf.get(field, "") or term
                status, reason = compare_term("delivery", bf[field], observed)
                compatible = status == "Differs"
                if status != "Meets":
                    state = "mismatch" if compatible or state == "mismatch" else "needs review"
                    add("delivery", key, [bf[field], observed],
                        f"{item} requires {bf[field][:50]}. Offer: {(observed or 'unspecified')[:50]}.",
                        "Blocking" if compatible else "Warning",
                        [evidence(bom, line, field), evidence(quote, offer, field) or
                         evidence(quote, field="delivery") or evidence(quote, offer)], review=not compatible,
                        detail={"expected": bf[field], "observed": observed or "Unstated", "reason": reason,
                                "action": "Confirm the delivery commitment."})
            if state == "pass" and any(
                value and field not in {"preferred_brand", "unit_price", "amount"} and
                (field not in row["anchors"] or row["anchors"][field].get("weak"))
                for row in (line, offer) for field, value in row["fields"].items()
            ):
                state = "needs review"
            matrix[key] = {"state": state, "offers": [qf]}

        terms = quote["terms"]
        reasons = []
        declared_currency = document_currency(quote)
        currency = declared_currency or "PHP"
        cost_fields = ("subtotal", "total", "tax", "tax_declaration", "vat", "freight", "handling",
                       "charges", "discount", "currency", "labor_equipment", "mobilization")
        if any(terms.get(field) and (
            not (anchor := evidence(quote, field=field)) or anchor.get("weak")
        ) for field in cost_fields):
            reasons.append("Commercial values lack reliable source evidence")
        if scope_mode == "unknown" or not required:
            reasons.append("Purchase scope is unset or empty")
        if summary:
            reasons.append("Summary quotation. Item coverage, quantities and specifications cannot be verified.")
        elif quote["coverage"] != "full":
            reasons.append(f"Supplier coverage is {quote['coverage']}")
        if terms.get("draft_notice"):
            reasons.append("The source is marked draft or not issued.")
        if len(supplier_docs[norm(quote["supplier"])]) > 1:
            reasons.append("Multiple offers from this supplier need review")
        if not summary and any(row["state"] != "pass" for row in matrix.values()):
            reasons.append("Scope, specifications or quantities are unresolved")
        if (uncertain and not summary) or uncertain_source(bom):
            reasons.append("Extraction or line matching requires review")
        if scope_mode == "selected" and any(key not in required for key in offered):
            reasons.append("Quote includes items outside purchase scope")
        conflicting = set()
        for field, mentions in quote["term_mentions"].items():
            if field in quote.get("corrected_terms", []):
                continue
            if terms_conflict(field, mentions, declared_currency):
                conflicting.add(field)
                if field in cost_fields:
                    reasons.append(f"Conflicting {field} terms")
                readings = "\n".join(dict.fromkeys(mention["value"] for mention in mentions))
                reason = f"The offer contains conflicting {field.replace('_', ' ')} entries."
                add("terms", [quote["id"], field], field, reason, "Warning", term_anchors(quote, field), review=True,
                    detail={"observed": readings, "reason": reason, "action": "Confirm which source entry applies."},
                    editors=[term_editor(quote, field)])
        if any(reading[0] and reading[0] != currency for field in MONEY_TERMS
               if (reading := monetary_value(terms.get(field, "")))):
            reasons.append("Stated amounts use different currencies")

        subtotal = decimal_value(terms.get("subtotal", ""))
        materials = reconcile_materials(bom, quote, scope_mode, scope) if summary else None
        if materials and (rule := materials["finding"]):
            expected, offered = materials["expected"], materials["offered"]
            currency_label = materials["currency"]
            reconciliation = rule == "materials_reconciliation"
            field = "scoped_materials_total" if scope_mode == "selected" else "materials_total"
            action = ("Link the documented materials adjustment or correct the stated baseline amounts."
                      if reconciliation else "Check both stated materials totals against their source.")
            editors = [term_editor(bom, "materials_adjustment")] if reconciliation else [
                term_editor(bom, field), term_editor(quote, field),
            ]
            materials["finding"] = add(
                rule, quote["id"], [expected, offered, materials["subtotal"], materials["adjustments"]],
                materials["reason"], "Info" if rule == "materials_price_variance" else "Warning",
                materials["evidence"], review=rule == "materials_reconciliation",
                detail={"expected": f"{currency_label} {expected}",
                        "observed": f"{currency_label} {materials['computed'] if reconciliation else offered}",
                        "reason": materials["reason"], "action": action},
                editors=editors,
            )
        computed = sum(line_amounts, Decimal(0))
        if bad_lines:
            reasons.append("One or more line amounts cannot be verified")
        if subtotal is not None and line_amounts and len(line_amounts) == len(quote["lines"]):
            if money(subtotal) != money(computed):
                reasons.append("Quoted subtotal differs from summed line amounts")
                add("subtotal", quote["id"], [money(computed), money(subtotal)],
                    f"Line amounts sum to {money(computed)}. The quote states {money(subtotal)}.",
                    "Warning", [evidence(quote, field="subtotal"), *[
                        evidence(quote, line, "amount") for line in quote["lines"]
                    ]])
        basis, price_treatment, declaration, ambiguous_tax = vat_treatment(
            terms.get("tax", ""), terms.get("tax_declaration", ""))
        vat_reading = monetary_value(terms.get("vat", ""))
        vat = vat_reading[1] if vat_reading else None
        rate_candidates = [terms.get("tax", ""), terms.get("tax_declaration", ""),
                           *(entry["value"] for entry in quote["term_mentions"].get("tax", [])
                             if "tax" not in quote.get("corrected_terms", []) and not entry["anchor"].get("weak"))]
        rate_text = next((match for value in rate_candidates
                          if (match := re.search(r"(\d+(?:\.\d{1,3})?)\s*%", value))), None)
        rate = Decimal(rate_text[1]) / 100 if rate_text else None
        tax_anchors = [anchor for field in ("tax", "tax_declaration", "vat") for anchor in term_anchors(quote, field)]
        tax_reason, tax_status, vat_finding = "", basis, None
        if ambiguous_tax or conflicting & {"tax", "tax_declaration", "vat"}:
            basis, tax_status = "unknown", "Ambiguous or conflicting"
            tax_reason = "VAT basis unclear. The quotation uses ambiguous or conflicting VAT wording."
        elif basis == "unknown":
            tax_status = "Basis unstated"
            if vat is None:
                tax_reason = "VAT basis unstated. Confirm how VAT applies to the quoted prices."
            else:
                symbol = "₱" if currency == "PHP" else f"{currency} "
                tax_reason = f"VAT basis unclear. The quote lists {symbol}{vat:,.2f} VAT but does not state how VAT applies to the prices."
        elif basis in {"Declared non-VAT", "VAT exempt", "Zero-rated"} and vat not in (None, Decimal(0)):
            tax_status = "Conflicting amount"
            tax_reason = f"The quote states {basis} but also lists a nonzero VAT amount."
        elif basis == "Mixed VAT treatment":
            tax_reason = "Mixed VAT treatment. Confirm which amounts are taxable, exempt or zero-rated."
        elif basis == "VAT exclusive" and ((vat is None and rate is None) or vat is not None and vat < 0):
            tax_status = "VAT inputs incomplete"
            tax_reason = "VAT exclusive. The quote does not establish the VAT amount or inputs needed to compute the payable total."
        elif basis == "VAT exclusive" and vat is None and any(
            charge_treatment(terms[field])[1] != 0 for field in COST_LABELS if terms.get(field)
        ):
            tax_status = "VAT inputs incomplete"
            tax_reason = "VAT exclusive. The quote does not establish how VAT applies to the separately stated charges or discounts."
        if not tax_anchors:
            tax_anchors = document_term_reference(quote, "The quote does not establish how VAT applies.")
        if tax_reason:
            reasons.append(tax_reason)
            field = "vat" if basis == "VAT exclusive" else "tax"
            vat_finding = add("tax", quote["id"], basis, tax_reason, "Warning", tax_anchors,
                              review=True, editors=[term_editor(quote, field)],
                              detail={"observed": terms.get("tax") or terms.get("tax_declaration") or terms.get("vat", "Unstated"),
                                      "reason": tax_reason, "action": "Confirm the supplier’s VAT declaration."})
        costs = [{"field": "vat", "label": "VAT", "value": terms.get("tax", "") or terms.get("tax_declaration", ""),
                  "price_treatment": price_treatment, "declaration": terms.get("tax_declaration", "") or declaration,
                  "quoted_amount": terms.get("vat", ""), "status": tax_status, "reason": tax_reason,
                  "evidence": tax_anchors, "finding": vat_finding}]
        charges = {}
        for field, label in COST_LABELS.items():
            raw = terms.get(field, "")
            status, value = charge_treatment(raw)
            if field in conflicting:
                status, value = "Conflicting", None
            if (read := monetary_value(raw)) and read[0] and read[0] != currency:
                status, value = "Different currency", None
            if value is not None and value < 0:
                status, value = "Sign unclear", None
            charges[field] = value
            if not (field == "freight" or raw):
                continue
            anchors = term_anchors(quote, field)
            reason = ""
            finding = None
            if value is None:
                action = ("Confirm whether delivery is included or separately charged." if field == "freight"
                          else f"Confirm how {label.lower()} affects the payable total.")
                reason = f"{label} {status.lower()}. {action}"
                reasons.append(f"{field.capitalize()} is unclear")
                anchors = anchors or document_term_reference(quote, f"No {label.lower()} term was established in this quote.")
                finding = add("charges", [quote["id"], field], field, reason, "Warning", anchors, review=True,
                              detail={"observed": raw or "Unstated", "reason": reason, "action": action},
                              editors=[term_editor(quote, field)])
            costs.append({"field": field, "label": label, "value": raw, "status": status, "reason": reason,
                          "evidence": anchors, "finding": finding})
        freight, extra, handling, discount = (charges[field] for field in ("freight", "charges", "handling", "discount"))
        if not summary and any(charges[field] for field in ("labor_equipment", "mobilization")):
            reasons.append("Labor or mobilization is priced separately from the material comparison")

        declared_total = decimal_value(terms.get("total", ""))
        payable = None
        if (not summary and not bad_lines and freight is not None and not tax_reason
                and all(charges[field] is not None for field in ("handling", "charges", "discount") if field in terms)):
            base = computed + freight + (extra or 0) + (handling or 0) - (discount or 0)
            if basis == "VAT exclusive":
                if vat is not None and vat >= 0:
                    payable = base + vat
                elif rate is not None and freight == 0 and not extra and not handling and not discount:
                    payable = base + Decimal(money(computed * rate))
                else:
                    reasons.append("VAT basis for adjustments is unspecified")
            else:
                payable = base
            if declared_total is not None and payable is not None and money(declared_total) != money(payable):
                reasons.append("Quoted total differs from documented arithmetic")
                add("total", quote["id"], [money(payable), money(declared_total)],
                    f"Documented amounts sum to {money(payable)}. The quote states {money(declared_total)}.",
                    "Warning", [evidence(quote, field=field) for field in ("subtotal", "freight", "vat", "handling",
                                                                        "charges", "discount", "total") if terms.get(field)] + [
                        evidence(quote, line, "amount") for line in quote["lines"]
                    ])
            if declared_total is None and (extra is None or discount is None):
                reasons.append("Payable total or explicit adjustments are missing")
            if payable is not None and payable < 0:
                reasons.append("Documented adjustments imply a negative payable total")
        timing = validity_interpretation(terms, as_of)
        timing_anchors = term_anchors(quote, "validity")
        if timing["duration"]:
            timing_anchors.extend(term_anchors(quote, "quote_date"))
        timing["evidence"] = timing_anchors
        timing["finding"] = None
        timing_fields = {"validity", "quote_date"} if timing["duration"] else {"validity"}
        if terms.get("validity") and (conflicting & timing_fields or any(
            terms.get(field) and (not (anchor := evidence(quote, field=field)) or anchor.get("weak"))
            for field in timing_fields
        )):
            timing.update(status="Needs clarification", expiry=None,
                          reason="The validity wording or quotation date needs a reliable, unambiguous source reading.")
        if timing["status"] in {"Expired", "Needs clarification"}:
            expired = timing["status"] == "Expired"
            if expired:
                reasons.append("Offer validity has expired")
            timing["finding"] = add("validity", quote["id"], timing["expiry"] or "unclear", timing["reason"],
                                    "Warning" if expired else "Info", timing_anchors, review=not expired,
                                    detail={"observed": timing["verbatim"], "reason": timing["reason"],
                                            "action": "Obtain a current quotation." if expired else "Confirm the offer expiry."},
                                    editors=[term_editor(quote, "validity")] + ([term_editor(quote, "quote_date")]
                                                                              if timing["duration"] else []))

        commercial_terms = commercial_requirements(quote, buyers, currency, add)
        if date_value(bom["terms"].get("delivery", "")) and any(
            entry["field"] == "delivery" and entry["status"] != "Meets" for entry in commercial_terms
        ):
            reasons.append("Delivery requirement is unresolved")
        if currency != "PHP":
            reasons.append("Currency differs from the PHP comparison basis")
        if quote.get("work_lines"):
            reasons.append("Some quoted work lacks a separate material breakdown")
        comparisons.append({
            "document": quote["id"], "supplier": quote["supplier"], "coverage": quote["coverage"],
            "items": matrix, "terms": terms, "tax_basis": basis, "currency": currency,
            "subtotal": money(subtotal) if subtotal is not None else None,
            "line_sum": money(computed) if line_amounts else None,
            "total": money(payable) if payable is not None and not reasons else None,
            "reasons": list(dict.fromkeys(reasons)), "expiry": timing["expiry"],
            "materials": materials,
            "commercial": {"terms": commercial_terms, "costs": costs, "validity": timing},
        })

    for issue in issues.values():
        fingerprint_data = {field: value for field, value in issue.items() if field not in {"detail", "source_editors"}}
        fingerprint_data["evidence"] = [{field: value for field, value in anchor.items() if field != "quality_reason"}
                                        for anchor in issue["evidence"]]
        issue["fingerprint"] = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    return {
        "issues": sorted(issues.values(), key=lambda issue: rank[issue["severity"]]),
        "comparison": sorted(comparisons, key=lambda row: (
            row["total"] is None, Decimal(row["total"] or "0"), row["supplier"]
        )),
        "matches": matches,
    }
