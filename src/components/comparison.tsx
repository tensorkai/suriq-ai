"use client";

import { History, Table2 } from "lucide-react";
import { amount, documentFeedback, lineFields, procurementRows, reviews, termFields } from "./model";
import type { Batch } from "./model";
import { Empty, Pill } from "./shared";

export function SupplierComparison({ batch }: { batch: Batch }) {
  if (!batch.comparison.length) return <Empty icon={<Table2 size={25} />} title="No supplier comparison yet">
    <p>Run verification to compare covered materials, costs, and terms.</p>
  </Empty>;
  const suppliers = batch.comparison;
  const bom = batch.documents.find((doc) => doc.role === "bom");
  const rows = procurementRows(bom?.lines ?? []).filter((line) => batch.scope_mode === "all" ||
    (batch.scope_mode === "selected" && batch.scope.includes(line.id)));
  const summaryTerms = ["materials_total", "labor_equipment", "mobilization", "total", "advance_payment", "balance_payment", "draft_notice"]
    .filter((field) => suppliers.some((row) => row.terms[field]));
  const terms = [...summaryTerms, "quote_date", "reference", "freight", "charges", "discount", "vat", "delivery", "validity", "payment"];
  return <section className="comparison">
    <div className="detail-heading"><h2>Offer comparison</h2></div>
    <div className="table-scroll"><table className="data-table matrix">
      <thead><tr><th scope="col">Comparison basis</th>{suppliers.map((row) => <th scope="col" key={row.document}>{row.supplier}</th>)}</tr></thead>
      <tbody>
        <tr><th scope="row">Comparable total</th>{suppliers.map((row) => <td key={row.document}>
          {row.total === null ? <><Pill value="Incomparable" /><ul>{row.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul></>
            : <strong className="total-amount">{amount(row.total, row.currency)}</strong>}
        </td>)}</tr>
        <tr><th scope="row">Declared coverage</th>{suppliers.map((row) => <td key={row.document}>{row.coverage}</td>)}</tr>
        <tr><th scope="row">VAT basis</th>{suppliers.map((row) => <td key={row.document}>{row.tax_basis}</td>)}</tr>
        <tr><th scope="row">Line sum / stated subtotal</th>{suppliers.map((row) => <td key={row.document}>
          {amount(row.line_sum, row.currency)} / {amount(row.subtotal, row.currency)}</td>)}</tr>
        {terms.map((field) => <tr key={field}><th scope="row">{termFields[field]}</th>
          {suppliers.map((row) => <td key={row.document}>{row.terms[field] || "Unknown"}</td>)}</tr>)}
        <tr><th scope="row">Outstanding findings</th>{suppliers.map((row) => {
          const issues = batch.issues.filter((issue) => issue.evidence.some((anchor) => anchor.document === row.document) &&
            issue.review.status !== "false_positive");
          return <td key={row.document}>{issues.length ? <ul>{issues.map((issue) =>
            <li key={issue.id}>{issue.title} · {reviews[issue.review.status]}</li>)}</ul> : "None"}</td>;
        })}</tr>
        {rows.length > 0 && <tr className="matrix-section"><th colSpan={suppliers.length + 1}>Purchase scope ({rows.length} materials)</th></tr>}
        {rows.map((line) => <tr key={line.id}><th scope="row">
          {line.fields.material || line.fields.description}<small>{line.fields.code || line.id} · Required {line.fields.quantity || "—"} {line.fields.unit}</small>
        </th>{suppliers.map((row) => {
          const item = row.items[line.id];
          return <td key={row.document}><Pill value={item?.state ?? "Unknown"} />
            {item?.offers.map((offer, index) => <div key={index} className="matrix-offer">
              <strong>{offer.quantity || "—"} {offer.unit} · {amount(offer.unit_price, row.currency)}</strong>
              <small>{[offer.material || offer.description, offer.specification, offer.pack, offer.grade].filter(Boolean).join(" · ")}</small>
            </div>)}</td>;
        })}</tr>)}
      </tbody>
    </table></div>
  </section>;
}

const activityLabels: Record<string, string> = {
  create_batch: "Batch created",
  rename_batch: "Batch renamed",
  import_document: "Document added",
  extract_document: "Document read",
  correct_role: "Document type corrected",
  retry_extraction: "Document sent for another reading",
  delete_document: "Document removed",
  set_scope: "Purchase scope updated",
  set_coverage: "Supplier coverage updated",
  correct_terms: "Document terms corrected",
  add_line: "Material row added",
  correct_line: "Material row corrected",
  verify_batch: "Verification run",
  review_issue: "Finding reviewed",
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function Activity({ batch }: { batch: Batch }) {
  if (!batch.history.length) return <Empty icon={<History size={23} />} title="No activity yet" />;
  const documents = new Map(batch.documents.map((doc) => [doc.id, doc.name]));
  return <section className="comparison">
    <div className="detail-heading"><h2>Batch activity</h2></div>
    <div className="activity-list">{batch.history.map((event) => {
      const data = record(event.data);
      const file = typeof data.document === "string" ? documents.get(data.document) : undefined;
      const name = file ?? (typeof data.name === "string" ? data.name : undefined);
      const note = [data.note, data.reason, data.message].find((value): value is string => typeof value === "string" && Boolean(value));
      const prior = record(data.before);
      const previousValues = record(prior.values ?? prior.fields);
      const values = Object.entries(record(record(data.after).values)).filter(([field, value]) => previousValues[field] !== value);
      const transition = typeof data.before === "string" && typeof data.after === "string"
        ? [data.before, data.after] : undefined;
      const status = typeof data.status === "string" && data.status in reviews ? reviews[data.status as keyof typeof reviews] : undefined;
      return <article key={event.id} className="activity-event">
        <div><strong>{activityLabels[event.action] ?? "Batch updated"}</strong>
          <time dateTime={event.created}>{new Date(event.created).toLocaleString("en-PH")}</time></div>
        {(name || status) && <p>{[name, status].filter(Boolean).join(" · ")}</p>}
        {note && <p>{documentFeedback(note)}</p>}
        {transition && <p>{transition[0]} → {transition[1]}</p>}
        {values.length > 0 && <dl className="activity-values">{values.map(([field, value]) =>
          <div key={field}><dt>{lineFields[field] ?? termFields[field] ?? field}</dt><dd>{previousValues[field] ? `${String(previousValues[field])} → ` : ""}{String(value || "—")}</dd></div>)}</dl>}
      </article>;
    })}</div>
  </section>;
}
