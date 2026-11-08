export type Coverage = "full" | "partial" | "unknown";
export type Scope = "all" | "selected" | "unknown";
export type Box = [number, number, number, number];
export type Anchor = { source: number | string; boxes?: Box[]; cells?: string[]; text: string; weak?: boolean };
export type Source = {
  ref: number | string;
  method: "text" | "ocr" | "cells";
  warning: string;
  width?: number;
  height?: number;
  rows?: { row: number; cells: Record<string, string> }[];
};
export type Line = {
  id: string; fields: Record<string, string>; anchors: Record<string, Anchor>;
  original: string; match?: string; factor?: string;
};
export type Document = {
  id: string; name: string; kind: "pdf" | "xlsx"; role: "bom" | "quote"; supplier: string;
  status: "ready" | "processing" | "needs_review" | "failed"; coverage: Coverage; error: string;
  lines: Line[]; sources: Source[]; terms: Record<string, string>; term_anchors: Record<string, Anchor>;
};
export type ReviewStatus = "open" | "confirmed" | "false_positive" | "clarification";
export type Issue = {
  id: string; rule: string; fingerprint: string; title: string; note: string;
  severity: "Blocking" | "Warning" | "Info"; evidence: (Anchor & { document: string })[];
  review_required: boolean; confirmable: boolean; review: { status: ReviewStatus; note: string };
};
export type Comparison = {
  document: string; supplier: string; coverage: Coverage; tax_basis: string; total: string | null;
  subtotal: string | null; line_sum: string | null; currency: string; terms: Record<string, string>;
  reasons: string[]; expiry: string | null;
  items: Record<string, { state: string; offers: Record<string, string>[] }>;
};
export type AuditEvent = { id: number; action: string; actor: string; created: string; data: unknown };
export type Batch = {
  id: string; name: string; project: string; project_name: string; verified_at: string | null; stale: number;
  scope_mode: Scope; scope: string[]; documents: Document[]; issues: Issue[]; comparison: Comparison[];
  matches: Record<string, { bom: string | null; candidates: string[]; method: string }>;
  history: AuditEvent[];
};
export type Project = { id: string; name: string; batches: Pick<Batch, "id" | "name" | "verified_at">[] };

export const lineFields: Record<string, string> = {
  code: "Item ID", material: "Material", description: "Description", quantity: "Quantity",
  unit: "Unit", specification: "Specification", dimension: "Dimension", grade: "Grade",
  standard: "Required standard", brand: "Required brand / offered brand", preferred_brand: "Preferred brand",
  pack: "Pack size", unit_price: "Unit price", amount: "Line amount",
  delivery_date: "Delivery date", lead_time: "Lead time",
};

export const termFields: Record<string, string> = {
  supplier: "Supplier shown on source", reference: "Quote reference", quote_date: "Quote date",
  validity: "Validity / expiry", currency: "Currency", delivery: "Delivery / needed by", payment: "Payment terms",
  freight: "Freight", charges: "Additional charges", discount: "Discount", tax: "VAT basis",
  vat: "VAT amount", subtotal: "Subtotal", total: "Payable total",
};

export const reviews: Record<ReviewStatus, string> = {
  open: "Open", confirmed: "Confirmed", false_positive: "False positive", clarification: "Needs clarification",
};

export function docName(doc: Document) {
  return doc.role === "bom" ? "Bill of materials" : doc.supplier;
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
    throw new Error(message || `Request failed (${response.status}).`);
  }
  return data as T;
}
