"use client";

import { History, Table2 } from "lucide-react";
import { useState } from "react";
import { amount, documentFeedback, isBaseline, lineFields, procurementRows, reviews, sourceName, supplierNameNeeded, termFields } from "./model";
import type { Batch, Document, EvidenceAnchor } from "./model";
import { Empty, Modal, Pill } from "./shared";
import { SourceViewer } from "./viewer";

const comparisonViews = ["Overview", "Terms", "Line items"] as const;

export function SupplierComparison({ batch, openFinding }: { batch: Batch; openFinding: (id: string) => void }) {
  const [view, setView] = useState<typeof comparisonViews[number]>("Overview");
  const [exceptions, setExceptions] = useState(false);
  const [source, setSource] = useState<EvidenceAnchor[]>([]);
  const summary = batch.summary;
  if (!batch.comparison.length || !summary) return <Empty icon={<Table2 size={25} />} title="No supplier comparison yet">
    <p>Run verification to compare covered materials, costs, and terms.</p>
  </Empty>;
  const comparison = new Map(batch.comparison.map((row) => [row.document, row]));
  const suppliers = summary.offers.map((offer) => comparison.get(offer.document)!);
  const documents = new Map(batch.documents.map((doc) => [doc.id, doc]));
  const bom = batch.documents.find((doc) => doc.id === batch.baseline_document) ?? batch.documents.find(isBaseline);
  const rows = procurementRows(bom?.lines ?? []).filter((line) =>
    (batch.scope_mode === "all" || (batch.scope_mode === "selected" && batch.scope.includes(line.id))) &&
    (!exceptions || suppliers.some((supplier) => supplier.items[line.id]?.state !== "pass"))
  );
  const summaryTerms = ["materials_total", "labor_equipment", "mobilization", "total", "advance_payment", "balance_payment", "draft_notice"]
    .filter((field) => suppliers.some((row) => row.terms[field]));
  const terms = [...summaryTerms, "quote_date", "reference", "freight", "handling", "charges", "discount", "vat",
    "delivery", "validity", "payment", "warranty", "exclusions"];
  const issueById = new Map(batch.issues.map((issue) => [issue.id, issue]));
  const sourceDoc = documents.get(source[0]?.document);

  function sourceButtons(anchors: EvidenceAnchor[]) {
    const groups = new Map<string, EvidenceAnchor[]>();
    for (const anchor of anchors) {
      const key = `${anchor.document}:${anchor.scope === "document" ? "document" : anchor.source}`;
      const values = groups.get(key) ?? [];
      values.push(anchor);
      groups.set(key, values);
    }
    return [...groups.entries()].map(([key, group]) => {
      const anchor = group[0];
      const file = documents.get(anchor.document)?.name;
      const location = anchor.scope === "document" ? "Document reference" : sourceName(anchor.source);
      return <button key={key} className="text-button summary-source" type="button" onClick={() => setSource(group)}>
        {[file, location, ...new Set(group.flatMap((item) => item.cells ?? []))].filter(Boolean).join(" · ")}
      </button>;
    });
  }
  return <section className="comparison">
    <div className="detail-heading">
      <div><h2>Supplier Quotation Summary</h2><p>Internal canvass</p>
        <p>{[summary.project, batch.name, summary.site, summary.scope].filter(Boolean).join(" · ")}</p>
        {summary.exclusions > 0 && <p>{summary.exclusions} baseline {summary.exclusions === 1 ? "row is" : "rows are"} outside the material comparison.</p>}
        <p className="fine-print">Verified {summary.verified}</p>
      </div>
    </div>
    <div className="inline comparison-controls">
      <nav className="segment" aria-label="Comparison view">{comparisonViews.map((item) =>
        <button type="button" key={item} aria-pressed={view === item} onClick={() => setView(item)}>{item}</button>)}</nav>
      {view === "Line items" && <label className="inline">
        <input type="checkbox" checked={exceptions} onChange={(event) => setExceptions(event.target.checked)} /> Exceptions only
      </label>}
    </div>
    {view === "Overview" ? <div className="table-scroll summary-scroll" tabIndex={0} role="region" aria-label="Supplier Quotation Summary table">
      <table className="data-table matrix summary-matrix">
        <thead><tr><th scope="col">Comparison basis</th>{summary.offers.map((offer) => <th scope="col" key={offer.document}>
          {offer.supplier}<small>{offer.comparable ? "Comparable" : "Incomparable"}</small>
        </th>)}</tr></thead>
        <tbody>{summary.fields.map((field) => <tr key={field.key}>
          <th scope="row">{field.label}</th>
          {summary.offers.map((offer) => {
            const cell = offer.cells[field.key];
            return <td key={offer.document}>
              <div className={field.key === "payable" && offer.comparable ? "total-amount summary-value" : "summary-value"}>
                {cell.value || "Unstated"}
              </div>
              {cell.reasons.length > 0 && <ul>{cell.reasons.map((reason) => <li key={reason}>{documentFeedback(reason)}</li>)}</ul>}
              {cell.findings.length > 0 && <ul className="comparison-findings">{cell.findings.slice(0, 3).map((id) => {
                const issue = issueById.get(id);
                return issue && <li key={id}><button className="text-button" type="button" onClick={() => openFinding(id)}>
                  {issue.title}</button><small>{issue.detail?.context}</small></li>;
              })}{cell.findings.length > 3 && <li>{cell.findings.length - 3} more in Findings</li>}</ul>}
              {sourceButtons(cell.evidence)}
            </td>;
          })}
        </tr>)}</tbody>
      </table>
    </div> : view === "Line items" && !rows.length ? <Empty icon={<Table2 size={22} />} title={exceptions ? "No line item exceptions" : "No line items in scope"} /> :
    <div className="table-scroll"><table className="data-table matrix">
      <thead><tr><th scope="col">{view === "Line items" ? "Required material" : "Comparison basis"}</th>{suppliers.map((row) => <th scope="col" key={row.document}>{supplierNameNeeded(row.supplier)
          ? <>Supplier name needed<small>{documents.get(row.document)?.name}</small></>
          : row.supplier}</th>)}</tr></thead>
      <tbody>
        {view === "Terms" && <>
          <tr><th scope="row">VAT basis</th>{suppliers.map((row) => <td key={row.document}>{row.tax_basis}</td>)}</tr>
          <tr><th scope="row">Line sum / stated subtotal</th>{suppliers.map((row) => <td key={row.document}>
            {amount(row.line_sum, row.currency)} / {amount(row.subtotal, row.currency)}</td>)}</tr>
          {terms.map((field) => <tr key={field}><th scope="row">{termFields[field] ?? field}</th>
            {suppliers.map((row) => {
              const doc = documents.get(row.document);
              const anchor = doc?.term_anchors[field];
              return <td key={row.document}>{row.terms[field] || "Unknown"}
                {anchor && sourceButtons([{ ...anchor, document: row.document }])}</td>;
            })}</tr>)}
        </>}
        {view === "Line items" && rows.map((line) => <tr key={line.id}><th scope="row">
          {line.fields.material || line.fields.description}<small>{line.fields.code || line.id} · Required {line.fields.material_quantity || line.fields.quantity || "—"} {line.fields.material_unit || line.fields.unit}</small>
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
    {sourceDoc && <SourcePreview document={sourceDoc} anchors={source} close={() => setSource([])} />}
  </section>;
}

function SourcePreview({ document, anchors, close }: { document: Document; anchors: EvidenceAnchor[]; close: () => void }) {
  return <Modal title="Source evidence" wide close={close}>
    <SourceViewer doc={document} initial={anchors[0]?.source} anchors={anchors} />
  </Modal>;
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
  bundle_layout: "Quotation bundle pages assigned",
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
        {note && <p className="summary-value">{documentFeedback(note)}</p>}
        {transition && <p>{transition[0]} → {transition[1]}</p>}
        {values.length > 0 && <dl className="activity-values">{values.map(([field, value]) =>
          <div key={field}><dt>{lineFields[field] ?? termFields[field] ?? field}</dt><dd>{previousValues[field] ? `${String(previousValues[field])} → ` : ""}{String(value || "—")}</dd></div>)}</dl>}
      </article>;
    })}</div>
  </section>;
}
