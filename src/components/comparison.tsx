"use client";

import { History, Table2 } from "lucide-react";
import { useState } from "react";
import { amount, coverageLabel, documentFeedback, lineFields, procurementRows, reviews, supplierNameNeeded, termFields } from "./model";
import type { Batch } from "./model";
import { Empty, Pill } from "./shared";

const comparisonViews = ["Overview", "Terms", "Line items"] as const;

export function SupplierComparison({ batch, openFinding }: { batch: Batch; openFinding: (id: string) => void }) {
  const [view, setView] = useState<typeof comparisonViews[number]>("Overview");
  const [exceptions, setExceptions] = useState(false);
  if (!batch.comparison.length) return <Empty icon={<Table2 size={25} />} title="No supplier comparison yet">
    <p>Run verification to compare covered materials, costs, and terms.</p>
  </Empty>;
  const suppliers = batch.comparison;
  const documents = new Map(batch.documents.map((doc) => [doc.id, doc]));
  const bom = batch.documents.find((doc) => doc.role === "bom");
  const rows = procurementRows(bom?.lines ?? []).filter((line) =>
    (batch.scope_mode === "all" || (batch.scope_mode === "selected" && batch.scope.includes(line.id))) &&
    (!exceptions || suppliers.some((supplier) => supplier.items[line.id]?.state !== "pass"))
  );
  const summaryTerms = ["materials_total", "labor_equipment", "mobilization", "total", "advance_payment", "balance_payment", "draft_notice"]
    .filter((field) => suppliers.some((row) => row.terms[field]));
  const terms = [...summaryTerms, "quote_date", "reference", "freight", "charges", "discount", "vat", "delivery", "validity", "payment"];
  const blockers = batch.issues.filter((issue) => issue.severity === "Blocking" && issue.review.status !== "false_positive");
  const blockerGroups = [
    { title: "Unresolved blockers", issues: blockers.filter((issue) => issue.review.status !== "confirmed") },
    { title: "Confirmed blockers", issues: blockers.filter((issue) => issue.review.status === "confirmed") },
  ];
  return <section className="comparison">
    <div className="detail-heading"><h2>Offer comparison</h2></div>
    <div className="inline comparison-controls">
      <nav className="segment" aria-label="Comparison view">{comparisonViews.map((item) =>
        <button type="button" key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item}</button>)}</nav>
      {view === "Line items" && <label className="inline">
        <input type="checkbox" checked={exceptions} onChange={(event) => setExceptions(event.target.checked)} /> Exceptions only
      </label>}
    </div>
    {view === "Line items" && !rows.length ? <Empty icon={<Table2 size={22} />} title={exceptions ? "No line item exceptions" : "No line items in scope"} /> :
    <div className="table-scroll"><table className="data-table matrix">
      <thead><tr><th scope="col">{view === "Line items" ? "Required material" : "Comparison basis"}</th>{suppliers.map((row) => <th scope="col" key={row.document}>{supplierNameNeeded(row.supplier)
          ? <>Supplier name needed<small>{documents.get(row.document)?.name}</small></>
          : row.supplier}</th>)}</tr></thead>
      <tbody>
        {view === "Overview" && <>
          <tr><th scope="row">Comparable total</th>{suppliers.map((row) => <td key={row.document}>
            {row.total === null ? <><Pill value="Incomparable" /><details className="comparison-reasons">
              <summary>Why this offer is incomparable</summary>
              <ul>{row.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            </details></> : <strong className="total-amount">{amount(row.total, row.currency)}</strong>}
          </td>)}</tr>
          <tr><th scope="row">Declared coverage</th>{suppliers.map((row) => <td key={row.document}>{coverageLabel[row.coverage]}</td>)}</tr>
          {blockerGroups.filter((group, index) => index === 0 || group.issues.length > 0).map((group) =>
            <tr key={group.title}><th scope="row">{group.title}</th>{suppliers.map((row) => {
              const issues = group.issues.filter((issue) => issue.evidence.some((anchor) => anchor.document === row.document));
              return <td key={row.document}>{issues.length ? <ul className="comparison-findings">{issues.map((issue) =>
                <li key={issue.id}><button className="text-button" type="button" onClick={() => openFinding(issue.id)}>
                  {issue.title}</button><small>{reviews[issue.review.status]}</small></li>)}</ul> : "None"}</td>;
            })}</tr>)}
        </>}
        {view === "Terms" && <>
          <tr><th scope="row">VAT basis</th>{suppliers.map((row) => <td key={row.document}>{row.tax_basis}</td>)}</tr>
          <tr><th scope="row">Line sum / stated subtotal</th>{suppliers.map((row) => <td key={row.document}>
            {amount(row.line_sum, row.currency)} / {amount(row.subtotal, row.currency)}</td>)}</tr>
          {terms.map((field) => <tr key={field}><th scope="row">{termFields[field]}</th>
            {suppliers.map((row) => <td key={row.document}>{row.terms[field] || "Unknown"}</td>)}</tr>)}
        </>}
        {view === "Line items" && rows.map((line) => <tr key={line.id}><th scope="row">
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
    </table></div>}
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
