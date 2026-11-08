"use client";

import { History, Table2 } from "lucide-react";
import { amount, reviews, termFields } from "./model";
import type { Batch } from "./model";
import { Empty, Pill } from "./shared";

export function SupplierComparison({ batch }: { batch: Batch }) {
  if (!batch.comparison.length) return <Empty icon={<Table2 size={25} />} title="No supplier comparison yet">
    <p>Run verification to compare covered materials, costs, and terms.</p>
  </Empty>;
  const suppliers = batch.comparison;
  const bom = batch.documents.find((doc) => doc.role === "bom");
  const rows = bom?.lines.filter((line) => batch.scope_mode === "all" ||
    (batch.scope_mode === "selected" && batch.scope.includes(line.id))) ?? [];
  const terms = ["quote_date", "reference", "freight", "charges", "discount", "vat", "delivery", "validity", "payment"];
  return <section className="comparison">
    <div className="detail-heading"><div><span className="eyebrow">SUPPLIER MATRIX</span><h2>Offer comparison</h2>
      <p>Totals are shown where purchase coverage, specifications, and documented cost basis align.</p></div></div>
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
        {rows.length > 0 && <tr className="matrix-section"><th colSpan={suppliers.length + 1}>PURCHASE SCOPE · {rows.length} MATERIALS</th></tr>}
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

export function Activity({ batch }: { batch: Batch }) {
  if (!batch.history.length) return <Empty icon={<History size={23} />} title="No audit events yet" />;
  return <section className="comparison">
    <div className="detail-heading"><div><span className="eyebrow">LOCAL AUDIT LOG</span><h2>Batch activity</h2>
      <p>Imports, extractions, scope changes, corrections, and review decisions.</p></div></div>
    <div className="activity-list">{batch.history.map((event) => <details key={event.id}>
      <summary><strong>{event.action.replaceAll("_", " ")}</strong><span>{event.actor} · {new Date(event.created).toLocaleString("en-PH")}</span></summary>
      <pre>{JSON.stringify(event.data, null, 2)}</pre>
    </details>)}</div>
  </section>;
}
