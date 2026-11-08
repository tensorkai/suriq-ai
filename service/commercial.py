"""Deterministic normalization of source-linked commercial terms."""
import re
from calendar import month_abbr, month_name
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")
MONTHS = {name.casefold(): number for names in (month_name, month_abbr)
          for number, name in enumerate(names) if name}
MONTHS["sept"] = 9
MONEY_TERMS = frozenset({
    "vat", "freight", "handling", "charges", "discount", "subtotal", "total", "materials_total",
    "item_extensions_total", "materials_adjustment", "labor_equipment", "mobilization",
    "advance_payment", "balance_payment",
})
COMMERCIAL_TERMS = {
    "payment": "Payment", "delivery": "Delivery", "warranty": "Warranty",
    "exclusions": "Exclusions", "validity": "Validity", "freight": "Freight",
    "handling": "Handling", "charges": "Other charges", "discount": "Discount",
}
COST_LABELS = {
    "freight": "Freight", "handling": "Handling", "charges": "Other charges",
    "discount": "Discount", "mobilization": "Mobilization and consumables",
    "labor_equipment": "Labor and equipment",
}
CURRENCY_PREFIX = r"(?:₱|US\$|PHP\.?|USD|EUR|GBP|JPY|AUD|CAD|SGD|HKD|CNY|AED|SAR)"
AMOUNT = rf"(?:(?P<currency>{CURRENCY_PREFIX})\s*)?(?P<number>[+-]?(?:\d{{1,3}}(?:,\d{{3}})+|\d{{1,15}})(?:\.\d{{1,8}})?)"
CUTOFF = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?)?(?:\s*(PHT|PHST|UTC|Z|[+-]\d{2}:\d{2}))?"
TRIGGERS = {
    "quotation date": r"(?:the )?(?:quotation|quote|offer|issue) date|date of (?:the )?(?:quotation|quote|offer|issue)",
    "purchase order": r"(?:purchase order|p\.?\s*o\.?)",
    "purchase order receipt": r"receipt of (?:the )?(?:purchase order|p\.?\s*o\.?)",
    "payment acceptance": r"(?:acceptance of (?:the )?payment|payment acceptance)",
    "payment receipt": r"(?:receipt of (?:the )?payment|payment receipt)",
    "quotation acceptance": r"(?:acceptance of (?:the )?(?:quotation|quote|offer)|(?:quotation|quote|offer) acceptance)",
    "completion": r"(?:completion(?: of (?:the )?works?)?)",
    "delivery": r"(?:delivery(?: of (?:the )?materials)?)",
    "award": r"(?:notice of award|award)",
    "notice to proceed": r"(?:notice to proceed|ntp)",
}
NUMBER_WORDS = {value: index for index, value in enumerate(
    ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"))}


def words(value):
    return " ".join(re.findall(r"[^\W_]+", value.casefold()))


def currency_code(value):
    code = value.strip().upper().rstrip(".")
    return {"₱": "PHP", "PESO": "PHP", "PESOS": "PHP", "US$": "USD"}.get(code, code)


def monetary_value(value):
    match = re.fullmatch(AMOUNT, value.strip(), re.I)
    if match is None:
        return None
    number = match["number"].replace(",", "")
    if len(number) > 22:
        return None
    return currency_code(match["currency"] or ""), Decimal(number)


def document_currency(doc):
    terms = doc["terms"]
    if terms.get("currency"):
        return currency_code(terms["currency"])
    readings = {parsed[0] for field in MONEY_TERMS for value in (
        terms.get(field, ""), *(entry["value"] for entry in doc.get("term_mentions", {}).get(field, []))
    ) if (parsed := monetary_value(value)) and parsed[0]}
    if len(readings) == 1:
        return readings.pop()
    return ""


def as_manila(value=None):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is None:
        return datetime.now(MANILA)
    if not isinstance(value, datetime):
        return datetime.combine(value, time.min, MANILA)
    return value.astimezone(MANILA) if value.tzinfo else value.replace(tzinfo=MANILA)


def date_reading(value):
    raw = re.sub(r"^\s*(?:valid (?:until|through)|until|through|expires(?:\s+on)?|(?:required|deliver)\s+by|by)\s+",
                 "", value, flags=re.I).strip().rstrip(".")
    if not raw:
        return None
    iso = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?)(Z|[+-]\d{2}:\d{2})?)?", raw, re.I)
    if iso:
        try:
            return date.fromisoformat(iso[1]) if not iso[2] else as_manila(datetime.fromisoformat(raw.upper()))
        except ValueError:
            return None
    local_iso = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s+at\s+(.+)", raw, re.I)
    if local_iso:
        try:
            return at_cutoff(date.fromisoformat(local_iso[1]), local_iso[2])
        except ValueError:
            return None
    reading = re.fullmatch(
        r"(?:(?P<day>\d{1,2})\s+(?P<month>[a-z]+)\.?|(?P<month_first>[a-z]+)\.?\s+(?P<day_last>\d{1,2}))"
        r"[,]?\s+(?P<year>\d{4})(?:\s+(?:at\s+)?(?P<cutoff>.+))?", raw, re.I)
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", raw)
    try:
        if reading:
            month = MONTHS.get((reading["month"] or reading["month_first"]).casefold())
            if not month:
                return None
            day = date(int(reading["year"]), month, int(reading["day"] or reading["day_last"]))
            return (at_cutoff(day, reading["cutoff"]) if reading["cutoff"] else day)
        if numeric:
            first, second, year = map(int, numeric.groups())
            if first > 12 >= second:
                return date(year, second, first)
            if second > 12 >= first:
                return date(year, first, second)
    except ValueError:
        return None
    return None


def at_cutoff(day, cutoff):
    clock = re.fullmatch(CUTOFF, cutoff, re.I)
    if not clock or not clock[2] and not clock[3]:
        return None
    hour = int(clock[1])
    if clock[3]:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if clock[3].casefold().startswith("p") else 0)
    zone = (clock[4] or "").upper()
    if zone in {"UTC", "Z"}:
        zone = "+00:00"
    elif zone in {"", "PHT", "PHST"}:
        zone = "+08:00"
    return as_manila(datetime.fromisoformat(f"{day.isoformat()}T{hour:02}:{int(clock[2] or 0):02}{zone}"))


def duration_reading(value):
    reading = re.search(r"(?<![\w./])(?P<count>\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)"
                        r"[-\s]+(?:(?P<basis>calendar|business|working)[-\s]+)?"
                        r"(?P<unit>day|week|month|year)s?\b", value, re.I)
    if not reading:
        return None
    count = reading["count"].casefold()
    suffix = value[reading.end():]
    triggered = re.search(r"\b(?:from and including|from|upon|after|starting on|following)\s+(.+)", suffix, re.I)
    trigger = "unspecified"
    if triggered:
        start = re.split(r"[,;]|\b(?:subject to|provided that|inclusive|including|at|through)\b",
                         triggered[1], maxsplit=1, flags=re.I)[0].strip(" .")
        trigger = next((name for name, pattern in TRIGGERS.items() if re.fullmatch(pattern, start, re.I)), start)
    qualifiers = []
    if re.search(r"\b(?:approx(?:imately)?|estimate[ds]?)\b", value, re.I):
        qualifiers.append("approximate")
    if re.search(r"\b(?:subject to|provided that|depending on|conditional|if|unless|tbc|tbd)\b", value, re.I):
        qualifiers.append("conditional")
    if re.search(r"\b(?:inclusive|including|as day (?:one|1))\b", value, re.I):
        qualifiers.append("inclusive")
    if re.search(r"\b(?:excluding|exclusive of)\b", value, re.I):
        qualifiers.append("exclusive")
    basis = (reading["basis"] or "unspecified").casefold()
    if basis == "working":
        basis = "business"
    return {"count": int(count) if count.isdigit() else NUMBER_WORDS[count], "unit": reading["unit"].casefold(),
            "day_basis": basis, "trigger": trigger, "qualifiers": qualifiers, "verbatim": value}


def duration_identity(reading):
    return tuple(reading[field] for field in ("count", "unit", "day_basis", "trigger")) + (tuple(reading["qualifiers"]),)


def timing_reading(value):
    absolute = date_reading(value)
    return {"verbatim": value, "date": absolute.isoformat() if absolute else None,
            "duration": None if absolute else duration_reading(value)}


def payment_milestones(value):
    milestones = {}
    for clause in re.split(r"[;\n]|,\s+(?=\d+(?:\.\d+)?\s*%)", value):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*(?:on|upon|after|at)\s+(.+)", clause, re.I)
        if not match:
            return {}
        text = re.sub(rf"\s+{CURRENCY_PREFIX}\s*[\d,.]+$", "", match[2], flags=re.I).strip(" .")
        trigger = next((name for name, pattern in TRIGGERS.items() if re.fullmatch(pattern, text, re.I)), None)
        if not trigger or trigger in milestones:
            return {}
        milestones[trigger] = Decimal(match[1])
    return milestones if sum(milestones.values()) == 100 else {}


def validity_interpretation(terms, as_of=None):
    checked = as_manila(as_of)
    raw = terms.get("validity", "")
    issued = date_reading(terms.get("quote_date", ""))
    absolute = date_reading(raw)
    duration = None if absolute else duration_reading(raw)
    expiry = absolute
    reason = ""
    if not raw:
        reason = "The quotation does not state its validity."
    elif duration:
        if {"approximate", "conditional"} & set(duration["qualifiers"]):
            reason = "The stated validity is approximate or conditional."
        elif duration["day_basis"] == "business":
            reason = "The quotation uses business days without an established business calendar."
        elif duration["day_basis"] != "calendar" or duration["unit"] not in {"day", "week"}:
            reason = "The quotation does not establish the calendar or business day basis."
        elif duration["trigger"] != "quotation date":
            reason = f"The validity start is unresolved ({duration['trigger']}). Confirm the triggering date."
        elif not issued:
            reason = "The quotation date is missing or ambiguous."
        else:
            start = issued.date() if isinstance(issued, datetime) else issued
            days = duration["count"] * (7 if duration["unit"] == "week" else 1)
            days -= int("inclusive" in duration["qualifiers"])
            if days >= 0:
                try:
                    expiry = start + timedelta(days=days)
                    cutoff = re.search(r"\bat\s+(.+)$", raw.rstrip("."), re.I)
                    if cutoff:
                        expiry = at_cutoff(expiry, cutoff[1])
                except (ValueError, OverflowError):
                    expiry = None
            if expiry is None:
                reason = "The validity duration or cutoff could not be applied."
    elif absolute is None:
        reason = ("The numeric expiry date is ambiguous. Confirm the date in words or YYYY-MM-DD."
                  if re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", raw)
                  else "The expiry wording could not be resolved to a date or supported duration.")
    expired = bool(expiry and (expiry < checked if isinstance(expiry, datetime) else expiry < checked.date()))
    return {"status": "Expired" if expired else "Valid" if expiry else "Needs clarification" if raw else "Unstated",
            "reason": reason or (f"The offer expired on {expiry.isoformat()}." if expired else ""),
            "expiry": expiry.isoformat() if expiry else None,
            "issue_date": issued.isoformat() if issued else None,
            "absolute_expiry": absolute.isoformat() if absolute else None,
            "duration": duration, "verbatim": raw, "as_of": checked.isoformat()}


def charge_treatment(value):
    key = words(value)
    amount = monetary_value(value)
    if not key:
        return "Unstated", None
    if amount:
        return ("Explicitly zero" if amount[1] == 0 else "Separately priced", amount[1])
    if re.search(r"\b(?:subject to|conditional|depends on|depending on|if|unless|estimated|approximate)\b", key):
        return "Conditional", None
    if re.search(r"\b(?:not included|excluded|separate quote|tbd|tbc|to be (?:confirmed|determined|advised))\b", key):
        return "Excluded / TBD", None
    if re.fullmatch(r"(?:included|inclusive)(?: in (?:the )?(?:price|total|quoted prices|quotation))?", key):
        return "Included", Decimal(0)
    if key in {"free", "free of charge", "free delivery", "waived", "none", "no charge", "no additional charges", "n a"}:
        return "Waived", Decimal(0)
    return "Unclear", None


def vat_treatment(value, declaration=""):
    key = words(value)
    terms = f"{key} {words(declaration)}"
    statuses = [
        ("Declared non-VAT", r"\bnon\s*vat\b"),
        ("VAT exempt", r"\bvat exempt\b|\bexempt (?:from vat|sales|transaction)\b"),
        ("Zero-rated", r"\bzero rated\b|\bvat zero rated\b"),
        ("Mixed VAT treatment", r"\bmixed (?:vat|tax)\b"),
    ]
    declared = [status for status, pattern in statuses if re.search(pattern, terms)]
    price = [
        ("VAT inclusive", r"\b(?:inclusive|included)\b|\bwith vat\b"),
        ("VAT exclusive", r"\b(?:exclusive|excluded)\b|\bplus vat\b"),
    ]
    priced = [status for status, pattern in price if re.search(pattern, terms)]
    ambiguous = (bool(re.search(r"\b(?:not|or|tbc|tbd|unknown|pending|unclear|if|subject to)\b", terms))
                 or len(declared) > 1 or len(priced) > 1 or bool(priced and declared and declared[0] != "Mixed VAT treatment"))
    treatment = "unknown" if ambiguous else next(iter(declared or priced), "unknown")
    return treatment, "Unclear" if ambiguous else next(iter(priced), ""), " / ".join(declared), ambiguous


def term_identity(field, value, currency="", scope=""):
    if field in MONEY_TERMS and (amount := monetary_value(value)):
        return "money", amount[0] or currency, amount[1], scope
    if field in {"quote_date", "delivery", "validity", "warranty"}:
        if reading := date_reading(value):
            return "date", reading.isoformat(), scope
        if reading := duration_reading(value):
            if not reading["qualifiers"]:
                return "duration", duration_identity(reading), scope
    if field == "tax":
        basis, _, _, ambiguous = vat_treatment(value)
        if basis != "unknown" and not ambiguous:
            rate = re.search(r"(\d+(?:\.\d+)?)\s*%", value)
            return "tax", basis, Decimal(rate[1]) if rate else None, scope
    return "text", " ".join(value.casefold().split()), scope


def compare_term(field, required, offered, required_currency="", offered_currency=""):
    if not offered:
        return "Needs clarification", "The quotation does not state this term."
    if field in {"delivery", "validity", "warranty"}:
        expected, observed = date_reading(required), date_reading(offered)
        if expected and observed and type(expected) is type(observed):
            meets = observed <= expected if field == "delivery" else observed >= expected
            return ("Meets" if meets else "Differs"), ("The offered date meets the requirement." if meets
                                                      else "The offered date differs from the buyer’s requirement.")
        left, right = duration_reading(required), duration_reading(offered)
        if left and right:
            if left["trigger"] != right["trigger"]:
                return "Needs clarification", f"The buyer counts from {left['trigger']}. The supplier counts from {right['trigger']}."
            keys = ("unit", "day_basis", "qualifiers")
            if (any(left[key] != right[key] for key in keys) or left["day_basis"] == "unspecified" and left["unit"] == "day"
                    or left["qualifiers"] or left["trigger"] == "unspecified"):
                return "Needs clarification", "Confirm the duration basis, start event and conditions."
            meets = right["count"] <= left["count"] if field == "delivery" else right["count"] >= left["count"]
            return ("Meets" if meets else "Differs"), ("The offered duration meets the requirement." if meets
                                                      else "The offered duration differs from the buyer’s requirement.")
        return "Needs clarification", "Confirm that the dates, durations and start events are comparable."
    if term_identity(field, required, required_currency) == term_identity(field, offered, offered_currency):
        return "Meets", "The documented terms agree."
    if field in COST_LABELS:
        wanted, actual = monetary_value(required), monetary_value(offered)
        expected_treatment, expected_amount = charge_treatment(required)
        _, actual_amount = charge_treatment(offered)
        if (expected_amount is not None and actual_amount is not None
                and (not wanted or not actual or (wanted[0] or required_currency) == (actual[0] or offered_currency))):
            if expected_treatment == "Included" and actual_amount == 0:
                return "Meets", "The quotation adds no separate amount."
            return ("Meets" if actual_amount == expected_amount else "Differs"), "Compare the explicitly stated charges."
    if field == "exclusions" and words(required) in {"none", "no exclusions", "no exclusions allowed"}:
        if re.search(r"\b(?:tbd|tbc|subject to|to be confirmed|unspecified)\b", words(offered)):
            return "Needs clarification", "The supplier’s exclusions are unresolved."
        return ("Meets" if words(offered) in {"none", "no exclusions"} else "Differs"), "The buyer requires no exclusions."
    if field == "payment":
        left, right = payment_milestones(required), payment_milestones(offered)
        if left and left.keys() == right.keys():
            return ("Meets" if left == right else "Differs"), "Compare the percentages for the same payment milestones."
    return "Needs clarification", "Confirm that the terms cover the same scope, units and conditions."
