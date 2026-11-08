"""Classify procurement purpose from independently corroborated source evidence."""
import re

REQUIREMENTS = re.compile(r"^\s*(?:project\s+)?(?:bill of (?:materials|quantities)|bom|boq|material requirements)\b(?![-.]\d)", re.I)
OFFER = re.compile(r"^\s*(?:(?:supplier|price|sales)\s+)?(?:quotation|quote|price proposal|we (?:hereby |are pleased to )?offer)\b", re.I)
UNSUPPORTED = re.compile(
    r"^\s*(?:tax invoice|sales invoice|invoice(?!\s+(?:address|to|billing))|purchase order|request for quotation|rfq|"
    r"material requisition|purchase requisition|purchase request|delivery receipt)\b", re.I
)
MODEL_OFFER = re.compile(
    r"\b(?:validity of (?:this |our )?offer|we (?:hereby |are pleased to )?offer|offer (?:is )?valid|"
    r"prices quoted|quotation (?:reference|validity))\b", re.I
)
OFFER_TERMS = {"supplier", "reference", "validity", "payment"}
COMMERCIAL = OFFER_TERMS | {"tax", "freight", "vat", "discount"}


def classify(snapshot):
    headings = snapshot.get("headings", [])
    requirements = [a for a in headings if REQUIREMENTS.search(a["text"])]
    offers = [a for a in headings if OFFER.search(a["text"])]
    unsupported = [a for a in headings if UNSUPPORTED.search(a["text"])]
    terms = {field: a for field, a in snapshot["term_anchors"].items() if field in COMMERCIAL}
    lines = snapshot["lines"]
    summaries = [snapshot["term_anchors"][field] for field in ("materials_total", "total")
                 if field in snapshot["term_anchors"] and not snapshot["term_anchors"][field].get("weak")
                 and re.fullmatch(r"(?:(?:PHP|₱)\s*)?\d[\d,]*(?:\.\d+)?", snapshot["terms"].get(field, ""), re.I)]
    prices = [row["anchors"][field] for row in lines for field in ("unit_price", "amount")
              if row["fields"].get(field) and field in row["anchors"]]
    quantities = [row["anchors"]["quantity"] for row in lines if row["fields"].get("quantity") and "quantity" in row["anchors"]]
    evidence = requirements + offers + list(terms.values())
    proposals = snapshot.get("model_classifications", [])
    # Model votes only participate when their cited text contains an independent role signal.
    proposed_bom = [anchor for item in proposals if item["role"] == "bom" for anchor in item["evidence"]
                    if REQUIREMENTS.search(anchor["text"])]
    proposed_offer = [anchor for item in proposals if item["role"] == "quote" for anchor in item["evidence"]
                      if OFFER.search(anchor["text"]) or MODEL_OFFER.search(anchor["text"])]
    native_offer_terms = {field for field in OFFER_TERMS & terms.keys() if terms[field].get("method") != "model"}
    estimated = [a for a in headings if re.search(r"\bestimated (?:unit )?(?:cost|price)\b", a["text"], re.I)]
    if unsupported:
        role, basis, evidence = "unsupported", "Source identifies an unsupported procurement document.", unsupported
    elif requirements and (offers or native_offer_terms - {"supplier"}):
        role, basis = "mixed", "Requirements and supplier-offer signals coexist. Inspect the source."
    elif (proposed_bom and (offers or proposed_offer)) or (proposed_offer and requirements):
        role, basis = "mixed", "The local interpretation found conflicting document purposes. Inspect the cited sections."
    elif lines and (requirements or (estimated and not offers and not native_offer_terms - {"supplier"})):
        role, basis = "bom", "Requirements or estimated-cost headings identify the material baseline."
        evidence += estimated
    elif offers and summaries and not lines:
        role, basis = "quote", "Quotation heading and priced summary are present. The offer is not itemized."
        evidence += summaries
    elif lines and (offers or proposed_offer or (prices and native_offer_terms)):
        role, basis = "quote", "An offer heading or commercial terms accompany the material lines."
        evidence += prices[:3] + proposed_offer
    elif lines and quantities and not prices and not terms and not proposed_offer:
        role, basis = "bom", "Material quantities appear without offer pricing or commercial terms."
        evidence += quantities[:3]
    else:
        role, basis = "unknown", "The extracted material and commercial evidence does not establish a document type."
        evidence += quantities[:3] + prices[:3]
    if role in {"bom", "quote"} and (not evidence or all(a.get("weak") for a in evidence)):
        role, basis = "unknown", "Document type relies on uncertain source readings."
    if role in {"unknown", "mixed"}:
        evidence += [anchor for proposal in proposals for anchor in proposal["evidence"]]
    return {"role": role, "basis": basis, "evidence": evidence[:12], "version": "rules-2.2",
            "summary_only": bool(role == "quote" and not lines and summaries)}
