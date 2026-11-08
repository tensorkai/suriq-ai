"""Deterministic issue grouping, scope checks and decimal commercial comparisons."""
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from zoneinfo import ZoneInfo

from service.extraction import ATTRIBUTES

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
RULES = {
    "dimension": "Dimension mismatch", "grade": "Grade requirement", "standard": "Standard requirement",
    "brand": "Required brand", "specification": "Specification mismatch", "material": "Material mismatch",
    "quantity": "Quantity mismatch", "unit": "Unit clarification", "pack": "Pack size mismatch",
    "missing": "Item not quoted", "extra": "Additional item", "match": "Material match needs review",
    "duplicate": "Duplicate offer lines", "line_math": "Line arithmetic", "subtotal": "Subtotal arithmetic",
    "total": "Payable total arithmetic", "tax": "VAT basis unclear", "charges": "Charges unclear",
    "validity": "Quotation validity", "delivery": "Delivery requirement",
    "extraction": "Source needs review", "terms": "Conflicting quote terms",
    "supplier_duplicate": "Multiple offers from supplier", "outside_scope": "Item outside purchase scope",
    "materials_summary": "Stated materials total difference",
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
    match = re.fullmatch(r"(?:valid until\s+)?(\d{4}-\d{2}-\d{2})(?: 00:00:00)?", value.strip(), re.I)
    try:
        return date.fromisoformat(match[1]) if match else None
    except ValueError:
        return None


def duration(value):
    match = re.fullmatch(r"(\d{1,3})\s*(calendar\s+|working\s+|business\s+)?(day|week)s?",
                         value.strip(), re.I)
    if not match:
        return None
    basis = (match[2] or "unspecified").strip().casefold()
    return (int(match[1]) * (7 if match[3].casefold() == "week" else 1), basis)


def pack_size(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kg|g|l)(?:\s*(?:/|per)\s*\w+)?",
                         value.strip(), re.I)
    if not match:
        return None
    n, kind = Decimal(match[1]), match[2].lower()
    return (n * Decimal(".001"), "kg") if kind == "g" else (n, kind)


def best_matches(line, baseline):
    code = norm(line["fields"].get("code", ""))
    by_code = [item["id"] for item in baseline if code and norm(item["fields"].get("code", "")) == code]
    if by_code:
        return by_code, "item ID"
    name = identity(line)
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
    key = norm(value)
    if re.search(r"\b(?:not|or|tbc|tbd|unknown|pending|unclear|if|subject to)\b", key):
        return "unknown"
    bases = [
        ("non-VAT", r"\bnon\s*vat\b|\bvat exempt\b"),
        ("VAT inclusive", r"\b(inclusive|included)\b|\bwith vat\b"),
        ("VAT exclusive", r"\b(exclusive|excluded)\b|\bplus vat\b"),
    ]
    detected = [basis for basis, pattern in bases if re.search(pattern, key)]
    return detected[0] if len(detected) == 1 else "unknown"


def charge(value):
    if norm(value) in {"included", "inclusive", "free", "none", "no charge", "no additional charges", "n a"}:
        return Decimal(0)
    return decimal_value(value)


def summary_quote(doc):
    if doc["role"] != "quote" or doc["lines"] or not doc.get("classification", {}).get("summary_only"):
        return False
    return any((amount := decimal_value(doc["terms"].get(field, ""))) is not None and amount >= 0
               and (anchor := evidence(doc, field=field)) and (
                   not anchor.get("weak") or anchor.get("method") == "model"
                   and doc.get("provenance", {}).get("provider") == "gemini"
               )
               for field in ("materials_total", "total"))


def uncertain_source(doc):
    return (any(source["warning"] for source in doc["sources"])
            or any(anchor.get("weak") for anchor in doc["term_anchors"].values())
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


def verify(documents, scope_mode, scope, as_of=None):
    with localcontext() as context:
        context.prec = 60
        return verify_in_context(documents, scope_mode, scope, as_of)


def verify_in_context(documents, scope_mode, scope, as_of):
    as_of = as_of or datetime.now(ZoneInfo("Asia/Manila")).date()
    bom = next(doc for doc in documents if doc["role"] == "bom")
    quotes = [doc for doc in documents if doc["role"] == "quote"]
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

    def add(rule, subject, condition, note, severity, anchors, *, review=False):
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
        for anchor in anchors:
            if anchor not in issue["evidence"]:
                issue["evidence"].append(anchor)

    for doc in documents:
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
        if not doc["lines"] and not summary_quote(doc):
            add("extraction", doc["id"], "no rows", "No material rows were recognized.", "Warning",
                source_evidence(doc), review=True)

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
                add(rule, key, [attribute_value(rule, expected), attribute_value(rule, actual)],
                    f"{item} requires {expected[:66]}, while the offer lists {actual[:66]}.",
                    "Warning" if review or weak else "Blocking", anchors, review=review)
                if not review and not weak:
                    state = "mismatch"
                elif state == "pass":
                    state = "needs review"

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
            if u1 in PACK_UNITS and u2 in PACK_UNITS and p1 and p2 and p1 != p2:
                difference("pack", bf["pack"], qf["pack"])
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
            elif u1 and (u1 == u2 or (u1 in {"bags", "sacks"} and u2 in {"bags", "sacks"} and p1 and p1 == p2)):
                if q1 != q2:
                    difference("quantity", f"{decimal_text(q1)} {u1}", f"{decimal_text(q2)} {u1}")
            elif u1 in METRIC and u2 in METRIC and METRIC[u1][0] == METRIC[u2][0]:
                if q1 * METRIC[u1][1] != q2 * METRIC[u2][1]:
                    difference("quantity", f"{decimal_text(q1)} {u1}", f"{decimal_text(q2)} {u2}")
            else:
                difference("unit", bf.get("unit", ""), qf.get("unit", ""), review=True)

            for field, parser in (("delivery_date", date_value), ("lead_time", duration)):
                if not bf.get(field):
                    continue
                expected = parser(bf[field])
                term = quote["terms"].get("delivery", "")
                actual = parser(qf.get(field, "") or term)
                compatible = expected is not None and actual is not None and (
                    field != "lead_time" or expected[1] == actual[1]
                )
                if not compatible or actual > expected:
                    state = "mismatch" if compatible or state == "mismatch" else "needs review"
                    add("delivery", key, [bf[field], qf.get(field, "") or term],
                        f"{item} requires {bf[field][:50]}. Offer: {(qf.get(field) or term or 'unspecified')[:50]}.",
                        "Blocking" if compatible else "Warning",
                        [evidence(bom, line, field), evidence(quote, offer, field) or
                         evidence(quote, field="delivery") or evidence(quote, offer)], review=not compatible)
            if state == "pass" and any(
                value and field not in {"preferred_brand", "unit_price", "amount"} and
                (field not in row["anchors"] or row["anchors"][field].get("weak"))
                for row in (line, offer) for field, value in row["fields"].items()
            ):
                state = "needs review"
            matrix[key] = {"state": state, "offers": [qf]}

        terms = quote["terms"]
        reasons = []
        cost_fields = ("subtotal", "total", "tax", "vat", "freight", "charges", "discount", "currency")
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
        for field, mentions in quote["term_mentions"].items():
            if field in quote.get("corrected_terms", []):
                continue
            if len({norm(mention["value"]) for mention in mentions}) > 1:
                reasons.append(f"Conflicting {field} terms")
                add("terms", [quote["id"], field], field, f"The offer contains conflicting {field} entries.",
                    "Warning", [{**m["anchor"], "document": quote["id"]} for m in mentions], review=True)

        subtotal = decimal_value(terms.get("subtotal", ""))
        if summary and scope_mode == "all" and quote["coverage"] != "partial":
            expected_raw = bom["terms"].get("item_extensions_total", "")
            stated_raw = terms.get("materials_total", "")
            expected, stated = decimal_value(expected_raw), decimal_value(stated_raw)
            anchors = [evidence(bom, field="item_extensions_total"), evidence(quote, field="materials_total")]
            php = all(re.match(r"^(?:PHP\.?|₱)", value, re.I) for value in (expected_raw, stated_raw))
            if (php and expected is not None and stated is not None and money(expected) != money(stated)
                    and all(anchor and not anchor.get("weak") for anchor in anchors)
                    and not bom.get("has_excluded_rows")):
                add("materials_summary", quote["id"], [money(expected), money(stated)],
                    f"Stated materials totals differ by PHP {money(abs(stated - expected))}. Confirm coverage and the source figures.",
                    "Warning", anchors, review=True)
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
        basis = tax_basis(terms.get("tax", ""))
        vat = decimal_value(terms.get("vat", ""))
        rate_text = re.search(r"(\d+(?:\.\d{1,3})?)\s*%", terms.get("tax", ""))
        rate = Decimal(rate_text[1]) / 100 if rate_text else None
        if basis == "unknown" or (basis == "VAT exclusive" and vat is None and rate is None):
            reasons.append("VAT basis or exclusive VAT amount is unknown")
            add("tax", quote["id"], basis, "The VAT basis or amount payable is not established.",
                "Warning", [evidence(quote, field="tax"), evidence(quote, field="vat"), *source_evidence(quote)],
                review=True)
        if basis == "non-VAT" and vat not in (None, Decimal(0)):
            reasons.append("Non-VAT declaration conflicts with a VAT amount")
            add("tax", quote["id"], "non-VAT amount", "A non-VAT declaration accompanies a VAT amount.",
                "Warning", [evidence(quote, field="tax"), evidence(quote, field="vat")])
        freight = charge(terms.get("freight", ""))
        extra = charge(terms["charges"]) if "charges" in terms else None
        discount = charge(terms["discount"]) if "discount" in terms else None
        for field, value in (("freight", freight), ("charges", extra), ("discount", discount)):
            if value is not None and value < 0:
                reasons.append(f"{field.capitalize()} has an unclear sign convention")
            # Missing freight is consequential. An absent extra charge/discount is only covered
            # when a declared payable total reconciles against the explicitly stated amounts.
            if value is None and (field == "freight" or field in terms):
                reasons.append(f"{field.capitalize()} is unclear")
                add("charges", [quote["id"], field], field, f"{field.capitalize()} is not established.",
                    "Warning", [evidence(quote, field=field), *source_evidence(quote)], review=True)

        declared_total = decimal_value(terms.get("total", ""))
        payable = None
        if not summary and not bad_lines and freight is not None and basis != "unknown":
            base = computed + freight + (extra or 0) - (discount or 0)
            if basis == "VAT exclusive":
                if vat is not None and vat >= 0:
                    payable = base + vat
                elif rate is not None and freight == 0 and not extra and not discount:
                    payable = base + Decimal(money(computed * rate))
                else:
                    reasons.append("VAT basis for adjustments is unspecified")
            else:
                payable = base
            if declared_total is not None and payable is not None and money(declared_total) != money(payable):
                reasons.append("Quoted total differs from documented arithmetic")
                add("total", quote["id"], [money(payable), money(declared_total)],
                    f"Documented amounts sum to {money(payable)}. The quote states {money(declared_total)}.",
                    "Warning", [evidence(quote, field=field) for field in ("subtotal", "freight", "vat",
                                                                        "charges", "discount", "total") if terms.get(field)] + [
                        evidence(quote, line, "amount") for line in quote["lines"]
                    ])
            if declared_total is None and (extra is None or discount is None):
                reasons.append("Payable total or explicit adjustments are missing")
            if payable is not None and payable < 0:
                reasons.append("Documented adjustments imply a negative payable total")
        quote_date = date_value(terms.get("quote_date", ""))
        expiry = date_value(terms.get("validity", ""))
        days = duration(terms.get("validity", ""))
        if not expiry and quote_date and days and days[1] == "calendar":
            try:
                expiry = quote_date + timedelta(days=days[0])
            except OverflowError:
                expiry = None
        if expiry and expiry < as_of:
            reasons.append("Offer validity has expired")
            add("validity", quote["id"], expiry.isoformat(), f"The offer expired on {expiry.isoformat()}.",
                "Warning", [evidence(quote, field="validity")] + ([evidence(quote, field="quote_date")] if days else []))
        elif terms.get("validity") and not expiry:
            add("validity", quote["id"], "unclear", "The offer expiry date needs clarification.", "Info",
                [evidence(quote, field="validity")], review=True)

        needed = date_value(bom["terms"].get("delivery", ""))
        if needed:
            delivery = date_value(terms.get("delivery", ""))
            if not delivery or delivery > needed:
                reasons.append("Delivery requirement is unresolved")
                add("delivery", "batch", [needed.isoformat(), delivery.isoformat() if delivery else "unspecified"],
                    f"Delivery is required by {needed}. The offer lists {delivery or 'no comparable date'}.",
                    "Blocking" if delivery else "Warning",
                    [evidence(bom, field="delivery"), evidence(quote, field="delivery") or
                     next(iter(source_evidence(quote)), None)], review=not delivery)
        currency = terms.get("currency", "PHP").strip().upper()
        currency = {"₱": "PHP", "PESO": "PHP", "PESOS": "PHP"}.get(currency, currency)
        if currency != "PHP":
            reasons.append("Currency differs from the PHP comparison basis")
        comparisons.append({
            "document": quote["id"], "supplier": quote["supplier"], "coverage": quote["coverage"],
            "items": matrix, "terms": terms, "tax_basis": basis, "currency": currency,
            "subtotal": money(subtotal) if subtotal is not None else None,
            "line_sum": money(computed) if line_amounts else None,
            "total": money(payable) if payable is not None and not reasons else None,
            "reasons": reasons, "expiry": expiry.isoformat() if expiry else None,
        })

    for issue in issues.values():
        issue["fingerprint"] = hashlib.sha256(json.dumps(issue, sort_keys=True).encode()).hexdigest()
    return {
        "issues": sorted(issues.values(), key=lambda issue: rank[issue["severity"]]),
        "comparison": sorted(comparisons, key=lambda row: (
            row["total"] is None, Decimal(row["total"] or "0"), row["supplier"]
        )),
        "matches": matches,
    }
