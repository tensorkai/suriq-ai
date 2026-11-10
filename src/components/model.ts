export type Coverage = "full" | "partial" | "unknown";
export const coverageLabel: Record<Coverage, string> = { full: "Full scope", partial: "Partial offer", unknown: "Coverage unknown" };
export type Scope = "all" | "selected" | "unknown";
export type Box = [number, number, number, number];
export type Anchor = { source: number | string; boxes?: Box[]; cells?: string[]; text: string; weak?: boolean; scope?: "page" | "document"; quality_reason?: string; method?: string };
export type Source = {
  ref: number | string;
  method: "text" | "ocr" | "cells";
  warning: string;
  width?: number;
  height?: number;
  schedule?: string;
  schedule_totals?: { label: string; value: string; anchor: Anchor }[];
  rows?: { row: number; cells: Record<string, string> }[];
};
export type Line = {
  id: string; fields: Record<string, string>; anchors: Record<string, Anchor>;
  original: string; match?: string; factor?: string; excluded?: boolean; match_baseline?: string;
  kind?: "material" | "service" | "work"; schedule?: string;
};
export type BundleLayout = {
  revision: number; undo_revision: number | null; pages: number[];
  offers: { supplier: string; reference: string; pages: number[] }[];
  reference_pages: number[]; pending_pages: number[]; excluded: { page: number; reason: string }[];
  reason: string;
};
export type Document = {
  id: string; name: string; kind: "pdf" | "xlsx" | "docx"; role: "bom" | "boq" | "buyer" | "quote" | "bundle" | "reference" | "unknown" | "mixed" | "unsupported";
  summary_only?: boolean; parser_update_available?: boolean;
  classification?: { role: string; document_type?: string; basis: string; evidence: Anchor[]; bundled_offers?: { supplier: string; pages: number[] }[] };
  provenance?: { parser: string; provider?: string; model?: string; model_digest?: string; inference: string };
  intake: string; supplier: string; processing_step: string; revises: string | null; superseded_by: string | null;
  bundle?: BundleLayout | null; bundle_parent?: string | null; bundle_pages?: number[]; bundle_active?: boolean;
  status: "ready" | "processing" | "needs_review" | "failed"; coverage: Coverage; error: string;
  lines: Line[]; sources: Source[]; terms: Record<string, string>; term_anchors: Record<string, Anchor>;
};
export type ReviewStatus = "open" | "confirmed" | "false_positive" | "clarification";
export type SourceEditor = { document: string; target: string; field: string; source?: number | string };
export type EvidenceAnchor = Anchor & { document: string };
export type ReferenceLink = {
  document: string; source: number; quote: string; supplier: string; reference: string; basis: string; scope: string;
  reference_value: string; original_value: string; matches: boolean; evidence: EvidenceAnchor[];
};
export type FindingDetail = {
  context: string; expected: string; observed: string; reason: string; action: string;
  source_labels: Record<string, string>;
};
export type Issue = {
  id: string; rule: string; fingerprint: string; title: string; note: string;
  severity: "Blocking" | "Warning" | "Info"; evidence: EvidenceAnchor[]; detail?: FindingDetail;
  search_hint?: string; source_editors?: SourceEditor[]; subject?: unknown;
  review_required: boolean; confirmable: boolean; review: { status: ReviewStatus; note: string };
};
export type Comparison = {
  document: string; supplier: string; coverage: Coverage; tax_basis: string; total: string | null;
  subtotal: string | null; line_sum: string | null; currency: string; terms: Record<string, string>;
  reasons: string[]; expiry: string | null;
  items: Record<string, { state: string; offers: Record<string, string>[] }>;
};
export type SummaryCell = { value: string; evidence: EvidenceAnchor[]; findings: string[]; reasons: string[] };
export type SupplierSummary = {
  project: string; site: string; scope: string; verified: string; exclusions: number;
  fields: { key: string; label: string }[];
  offers: { document: string; supplier: string; comparable: boolean; cells: Record<string, SummaryCell> }[];
};
export type AbstractBid = { state: string; unit_price: string; amount: string; lowest: boolean };
export type AbstractOfBids = {
  project: string; batch: string; site: string; scope: string; prepared: string;
  items: { number: number; id: string; description: string; quantity: string; unit: string; bids: Record<string, AbstractBid> }[];
  bidders: {
    document: string; supplier: string; reference: string; quote_date: string; materials: string; as_read: string;
    as_calculated: string; coverage: string; vat: string; delivery: string; validity: string; payment: string;
    rank: string; remarks: string[];
  }[];
  lowest: string | null; recommendation: string;
};
export type AuditEvent = { id: number; action: string; actor: string; created: string; data: unknown };
export type AIStatus = { provider: string; configured: boolean };

export type Batch = {
  ai: AIStatus;
  id: string; name: string; project: string; project_name: string; verified_at: string | null; stale: number;
  baseline_document?: string | null; verification_update_available?: boolean;
  verification_problem: string;
  verification_action: { kind: "upload" } | { kind: "type" | "scope" | "retry" | "manage" | "read"; document: string } | null;
  scope_mode: Scope; scope: string[]; documents: Document[]; issues: Issue[]; comparison: Comparison[]; summary?: SupplierSummary; abstract?: AbstractOfBids;
  reference_links?: ReferenceLink[];
  matches: Record<string, { bom: string | null; candidates: string[]; method: string }>;
  history: AuditEvent[];
};
export type Project = { id: string; name: string; batches: Pick<Batch, "id" | "name" | "verified_at">[] };

export const lineFields: Record<string, string> = {
  code: "Item ID", material: "Material", description: "Description", quantity: "Quantity",
  unit: "Unit", specification: "Specification", dimension: "Dimension", grade: "Grade",
  standard: "Required standard", brand: "Required brand / offered brand", preferred_brand: "Preferred brand",
  pack: "Pack size", unit_price: "Material unit price", amount: "Material extension",
  labor_unit_price: "Labor unit rate", labor_amount: "Labor extension", line_total: "Combined line total",
  delivery_date: "Delivery date", lead_time: "Lead time",
  material_quantity: "Separate material quantity", material_unit: "Separate material unit",
  material_rate: "Separate material rate", material_extension: "Separate material extension",
  equipment_rate: "Equipment unit rate", equipment_amount: "Equipment extension",
};

export const termFields: Record<string, string> = {
  supplier: "Supplier shown on source", reference: "Document reference", quote_date: "Quote date",
  validity: "Validity / expiry", currency: "Currency", delivery: "Delivery / needed by", payment: "Payment terms",
  freight: "Freight", charges: "Additional charges", discount: "Discount", tax: "VAT basis",
  vat: "VAT amount", subtotal: "Subtotal", total: "Stated quotation total",
  materials_total: "Stated materials total", item_extensions_total: "Sum of BOM item extensions",
  materials_adjustment: "Materials reconciliation adjustment", scope_reference: "Referenced materials baseline",
  materials_scope: "Materials scope", materials_scope_rows: "Materials scope item IDs",
  scoped_materials_total: "Selected scope materials total", project_site: "Project site",
  warranty: "Warranty", exclusions: "Exclusions", handling: "Handling", tax_declaration: "Declared VAT status",
  labor_equipment: "Labor and equipment", mobilization: "Mobilization and consumables",
  advance_payment: "Advance payment", balance_payment: "Balance payment", draft_notice: "Issuance status",
};

export const reviews: Record<ReviewStatus, string> = {
  open: "Open", confirmed: "Confirmed", false_positive: "False positive", clarification: "Needs clarification",
};

export function procurementRows(lines: Line[]) {
  return lines.filter((line) => !line.excluded && line.kind !== "service" && line.kind !== "work");
}

export function isBaseline(doc: Document) {
  return doc.role === "bom" || doc.role === "boq";
}

export function supplierNameNeeded(supplier: string) {
  return !supplier.trim() || /^unidentified supplier(?: [0-9a-f]{6})?$/i.test(supplier);
}

export function docName(doc: Document) {
  if (doc.role === "bom") return "Bill of materials";
  if (doc.role === "boq") return "Bill of quantities";
  if (doc.role === "buyer") return "Buyer requirements";
  if (doc.role === "reference") return "Quotation reference";
  if (doc.role === "quote") return supplierNameNeeded(doc.supplier) ? "Supplier name needed" : doc.supplier;
  if (doc.bundle || doc.classification?.bundled_offers?.length) {
    return doc.bundle?.offers.length === 0 && !doc.bundle.pending_pages.length
      ? "Quotation reference" : "Supplier quotation bundle";
  }
  if (doc.role === "mixed") return "Mixed document";
  if (doc.role === "unsupported") return "Unsupported document";
  return doc.status === "processing" ? "Identifying document" : "Type needs review";
}

export function hasPreciseSource(anchor: Anchor) {
  return Boolean(anchor.boxes?.length || anchor.cells?.length);
}

export function sourceName(source: number | string) {
  return typeof source === "number" ? `Page ${source}` : source;
}

export function amount(value: string | null | undefined, currency = "PHP") {
  if (value == null || value === "") return "—";
  const [whole, decimal] = value.split(".");
  const display = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${currency === "PHP" ? "₱" : `${currency} `}${display}${decimal === undefined ? "" : `.${decimal}`}`;
}

export function documentFeedback(value: string) {
  if (/^The local interpretation found conflicting/i.test(value)) {
    return "The document contains conflicting type indicators. Check the cited sections.";
  }
  if (/^(?:Confirm sharing with (?:Gemini|Meta)|AI assistance is disabled)/i.test(value)) {
    return "Matching suggestions are unavailable. Select a material manually.";
  }
  if (/^(?:AI model|Gemini|Meta|Local (?:Ollama|model|inference|AI|interpretation)|AI interpretation|The configured model|Configure an installed local Ollama model|Add a (?:Gemini API key|Meta MODEL_API_KEY))/i.test(value)) {
    if (/quota|rate limit|timeout|timed out|unreachable/i.test(value)) {
      return "Document reading is temporarily unavailable. Try again or review the source.";
    }
    if (/ambiguous|cited|source element|source\b.*uniquely|conflicts|unlinked|unrelated|duplicate/i.test(value)) {
      return "Some values could not be linked to the source with confidence. Review the affected page.";
    }
    if (/limit reached/i.test(value)) return "Some pages still need review. Check the source for missing rows.";
    return "Some document details could not be read. Review the source or try again.";
  }
  return value;
}

export async function api<T>(path: string, method = "GET", payload?: unknown): Promise<T> {
  const headers = new Headers({ "X-SuriQ-Request": "1" });
  const form = payload instanceof FormData;
  if (payload !== undefined && !form) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, {
    method, headers, cache: "no-store",
    body: payload === undefined ? undefined : form ? payload : JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = Array.isArray(detail) ? detail.map((entry) => entry.msg).join(" ") : detail;
    throw new Error(typeof message === "string" && message ? documentFeedback(message) : "Could not complete the request. Try again.");
  }
  return data as T;
}
