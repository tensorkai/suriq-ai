"use client";

import { FileText, Link2, Pencil, Plus, RotateCcw, UploadCloud } from "lucide-react";
import { useState } from "react";
import { BundleDetails } from "./bundles";
import { api, coverageLabel, docName, documentFeedback, isBaseline, lineFields, procurementRows, sourceName, supplierNameNeeded, termFields } from "./model";
import type { Anchor, Batch, Coverage, Document, Line, Scope, SourceEditor } from "./model";
import { Empty, LoadingNotice, Modal, PendingButton, Pill, Spinner } from "./shared";
import { SourceViewer } from "./viewer";
import type { Link } from "./viewer";

const statusLabel = { ready: "Ready", processing: "Reading document", needs_review: "Needs review", failed: "Failed" };

export type DocumentDestination = {
  document: string; kind: "type" | "scope" | "retry" | "read" | "edit"; editor?: SourceEditor;
};

export function canChangeType(doc: Document) {
  return (doc.status === "ready" || doc.status === "needs_review") && doc.role !== "unsupported"
    && !doc.superseded_by && doc.bundle_active !== false && !doc.bundle && !doc.classification?.bundled_offers?.length;
}

// The classifier assigns each upload its role; the rail groups by that role.
const documentGroups: [string, (doc: Document) => boolean][] = [
  ["BOM / BOQ", (doc) => isBaseline(doc)],
  ["Supplier quotations", (doc) => doc.role === "quote"],
  ["Other documents", (doc) => !isBaseline(doc) && doc.role !== "quote"],
];

function supplierLabel(doc: Document) {
  return supplierNameNeeded(doc.supplier) ? "Supplier quotation" : doc.supplier;
}

export function Documents({ batch, update, upload, revise, manage, destination, onEditorClose }: {
  destination?: DocumentDestination; onEditorClose?: () => void;
  batch: Batch; update: (value: Batch) => void; upload: () => void; revise: (doc: Document) => void; manage: (doc: Document) => void;
}) {
  const [selected, select] = useState(destination?.document ?? "");
  const [mode, setMode] = useState<"extraction" | "source" | "terms">(
    destination?.editor?.target === "terms" ? "terms" : "extraction"
  );
  const doc = batch.documents.find((item) => item.id === selected) ?? batch.documents[0];
  const [editor, edit] = useState<Line | "terms" | "new" | null>(() => {
    const target = destination?.editor?.target;
    if (target === "terms" || target === "new") return target;
    return doc?.lines.find((line) => line.id === target) ?? null;
  });
  const [editorContext, setEditorContext] = useState(destination?.editor);
  const [previewAnchor, setPreviewAnchor] = useState<Anchor>();
  const [scope, setScope] = useState(destination?.kind === "scope");
  const [type, setType] = useState(destination?.kind === "type");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"coverage" | "retry" | null>(null);
  const boms = batch.documents.filter((item) => isBaseline(item) && !item.superseded_by);
  const bom = boms.find((item) => item.id === batch.baseline_document) ?? (boms.length === 1 ? boms[0] : undefined);
  const revisedFrom = batch.documents.find((item) => item.id === doc?.revises);
  const revisedTo = batch.documents.find((item) => item.id === doc?.superseded_by);
  const pendingRevision = batch.documents.some((item) => item.revises === doc?.id);
  const count = batch.scope_mode === "all" ? procurementRows(bom?.lines ?? []).length : batch.scope.length;

  if (!doc) return <Empty icon={<UploadCloud size={27} />} title="Upload your first source">
    <p>Add a BOM or BOQ and up to five supplier quotations in PDF, XLSX, or DOCX format.</p>
    <button className="button primary" type="button" onClick={upload}><Plus size={16} /> Upload documents</button>
  </Empty>;

  const scheduleSources = doc.sources.filter((source) => source.schedule);
  const schedules = [...new Set(scheduleSources.map((source) => source.schedule))];
  const materialCount = procurementRows(doc.lines).length;
  const workCount = doc.lines.filter((line) => line.kind === "work").length;
  const serviceCount = doc.lines.filter((line) => line.kind === "service").length;
  const buyer = doc.role === "buyer";
  const shownMode = buyer && mode === "extraction" ? "terms" : mode;
  const historical = doc.bundle_active === false;
  const parent = batch.documents.find((item) => item.id === doc.bundle_parent);

  function open(id: string, anchor?: Anchor) {
    select(id); setMode(anchor ? "source" : "extraction"); setError(""); setEditorContext(undefined);
    setPreviewAnchor(anchor);
  }

  async function changeCoverage(coverage: Coverage) {
    setBusy("coverage"); setError("");
    try {
      update(await api<Batch>(`/documents/${doc.id}/coverage`, "PATCH", { coverage }));
    } catch (error) {
      setError((error as Error).message);
    } finally { setBusy(null); }
  }

  async function retryExtraction() {
    setBusy("retry"); setError("");
    try {
      await api(`/documents/${doc.id}/retry`, "POST");
      update(await api<Batch>(`/batches/${batch.id}`));
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(null); }
  }

  return <div className="documents-layout">
    <aside className="document-rail">
      <div className="issue-rail-heading"><span>Documents</span><span>{batch.documents.length}</span></div>
      {documentGroups.map(([label, test]) => {
        const items = batch.documents.filter(test);
        const baselines = label === "BOM / BOQ";
        return items.length > 0 && <section key={label} className="document-group" aria-label={label}>
          <h3>{label}<span>{items.length}</span></h3>
          {baselines && boms.length > 1 && !bom && <button className="text-button" type="button" onClick={() => setScope(true)}>
            {boms.length} baselines found · choose one</button>}
          {items.map((item) => <button key={item.id} className={`document-choice ${doc.id === item.id ? "selected" : ""}`}
            type="button" aria-current={doc.id === item.id ? "true" : undefined}
            onClick={() => open(item.id)}>
            <span className={`file-glyph ${item.role}`}><FileText size={17} /></span>
            <span><strong>{item.role === "quote" ? supplierLabel(item) : docName(item)}</strong><small title={item.name}>{item.terms.reference || item.name}</small><span className="document-status">{item.status === "processing" && <Spinner />}{item.bundle_active === false ? "Historical split" : item.superseded_by ? "Superseded" : statusLabel[item.status]}{baselines && boms.length > 1 && bom?.id === item.id && " · Selected baseline"}</span></span>
          </button>)}
        </section>;
      })}
      <button className="button secondary full" type="button" onClick={upload}><Plus size={15} /> Add document</button>
    </aside>
    <section className="document-detail">
      <div className="detail-heading">
        <div><div className="inline"><h2>{docName(doc)}</h2>
          {doc.role === "quote" && <Pill value="Supplier quotation" />}
          {doc.role === "quote" && doc.classification?.document_type === "boq" && <Pill value="BOQ format" />}
          {doc.status !== "processing" && <Pill value={historical ? "Historical split" : revisedTo ? "Superseded" : statusLabel[doc.status]} />}</div>
          <p>{doc.name} · {doc.sources.length} {doc.kind === "pdf" ? "pages" : doc.kind === "docx" ? "sections" : "worksheets"}</p>
        </div>
        {!historical && <button className="button secondary" type="button" onClick={() => manage(doc)}>Manage file</button>}
      </div>
      <div className="detail-settings">
        {doc.role === "quote" && canChangeType(doc) && supplierNameNeeded(doc.supplier) &&
          <button className="button secondary" type="button" onClick={() => setType(true)}>Add supplier name</button>}
        {isBaseline(doc) ? <div className="inline">
          {boms.length > 1 && <Pill value={bom?.id === doc.id ? "Selected baseline" : "Available baseline"} />}
          <span className="muted">Purchase scope</span><Pill value={batch.scope_mode === "unknown" ? "Unset" : `${count} selected`} />
          <button className="button secondary" type="button" disabled={!boms.some((item) => procurementRows(item.lines).length && item.status !== "processing")}
            onClick={() => setScope(true)}>Set scope</button>
        </div> : doc.role === "quote" && !revisedTo && !historical ? <label className="inline">Supplier coverage<select disabled={busy !== null} aria-busy={busy === "coverage"} value={doc.coverage}
          onChange={(event) => void changeCoverage(event.target.value as Coverage)}>
          {Object.entries(coverageLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select>{busy === "coverage" && <span role="status" className="inline"><Spinner />Saving coverage…</span>}</label> : null}
        {doc.role === "quote" && !pendingRevision && !historical && doc.status !== "processing" &&
          <button className="button secondary" type="button" onClick={() => revise(doc)}>Upload revision</button>}
        {!historical && (doc.status === "failed" || doc.status === "needs_review" || doc.parser_update_available
          || doc.bundle && doc.status !== "processing") &&
          <PendingButton className="button secondary" type="button" disabled={busy !== null} pending={busy === "retry"}
            pendingLabel="Starting…" autoFocus={destination?.kind === "retry" && doc.id === destination.document} icon={<RotateCcw size={15} />} onClick={() => void retryExtraction()}>
            {doc.bundle ? "Read bundle again" : doc.parser_update_available ? "Read source again" : "Try reading again"}
          </PendingButton>}

      </div>
      {revisedFrom && <p className="notice">Revision of {revisedFrom.name}.</p>}
      {revisedTo && <p className="notice">Superseded by {revisedTo.name}.</p>}
      {parent && <p className="notice inline">Original pages {doc.bundle_pages?.join(", ")} from {parent.name}
        <button className="button secondary" type="button" onClick={() => open(parent.id)}>Open quotation bundle</button></p>}
      {!doc.bundle && Boolean(doc.classification?.bundled_offers?.length) && <div className="notice stack">
        <strong>Separate supplier offers</strong>
        <p>Read this PDF again to assign its pages in the workspace.</p>
        {doc.classification?.bundled_offers?.map((offer) => <a className="button secondary" key={offer.pages[0]}
          href={`/api/documents/${doc.id}/offer/${offer.pages[0]}`}>
          Download {offer.supplier} · {offer.pages.length === 1 ? `page ${offer.pages[0]}` : `pages ${offer.pages.join(", ")}`}
        </a>)}
      </div>}
      {canChangeType(doc) && (doc.role === "unknown" || doc.role === "mixed") && <div className="notice inline">
        <span>{doc.role === "mixed" ? "This file contains both requirements and offer details." : "Review how this file should be used."}</span>
        <button className="button secondary" type="button" onClick={() => setType(true)}>Confirm document type</button>
      </div>}
      {doc.terms.draft_notice && <p className="notice">Source marked draft or not issued. Confirm figures with the vendor before procurement.</p>}
      {workCount > 0 && <p className="notice">{workCount} {workCount === 1 ? "work item needs" : "work items need"} a separate material quantity and unit before comparison.</p>}
      {schedules.length > 0 && <details className="notice">
        <summary>{schedules.length} priced {schedules.length === 1 ? "schedule" : "schedules"} · {materialCount} material rows · {serviceCount} service rows</summary>
        {schedules.map((schedule) => <div key={schedule}>
          <p><strong>{schedule}</strong></p>
          {scheduleSources.filter((source) => source.schedule === schedule).map((source) => <div key={source.ref}>
            <p>{sourceName(source.ref)}</p>
            {source.schedule_totals?.length ? <dl className="terms-grid">
              {source.schedule_totals.map((term, index) => <div key={index}><dt>{term.label}</dt><dd>{term.value}</dd></div>)}
            </dl> : null}
          </div>)}
        </div>)}
        <p>Service rows are shown for reference. Purchase scope uses material rows from one schedule.</p>
      </details>}
      {doc.error && <p className="notice" role="status">{documentFeedback(doc.error)}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {doc.status === "processing" ? <LoadingNotice className="document-progress">Reading {doc.name}…</LoadingNotice>
        : doc.bundle ? <BundleDetails key={doc.id} doc={doc} layout={doc.bundle} update={update} open={open}
          references={(batch.reference_links ?? []).filter((item) => item.document === doc.id)}
          quotes={batch.documents.filter((item) => item.bundle_parent === doc.id && item.bundle_active !== false)} /> : <>
      <nav className="segment" aria-label="Document view">
        {([["extraction", "Material lines"], ["terms", "Document terms"], ["source", "Original source"]] as const)
          .filter(([value]) => !buyer || value !== "extraction").map(([value, label]) =>
            <button key={value} type="button" aria-pressed={shownMode === value} onClick={() => setMode(value)}>{label}</button>)}
      </nav>
      {shownMode === "source" ? <div className="source-single">
        <SourceViewer key={doc.id} doc={doc} initial={previewAnchor?.source} anchors={previewAnchor ? [previewAnchor] : []} />
      </div> : shownMode === "terms" ? <>
        <div className="table-heading"><h3>Extracted terms</h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length || historical}
            onClick={() => edit("terms")}><Pencil size={15} /> Edit terms</button></div>
        {Object.keys(doc.terms).length ? <dl className="terms-grid">
          {Object.entries(doc.terms).map(([field, value]) => <div key={field}><dt>{termFields[field] ?? field}</dt><dd>{value || "—"}
            {doc.term_anchors[field]?.quality_reason && <small className="muted"> {doc.term_anchors[field].quality_reason}</small>}</dd></div>)}
        </dl> : <p className="notice">No labeled terms were extracted. Inspect the source to enter terms.</p>}
      </> : <>
        <div className="table-heading"><h3>{schedules.length ? "Extracted schedule rows" : "Extracted materials"} <span>{doc.lines.length}</span></h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length || historical}
            onClick={() => edit("new")}><Plus size={15} /> Add linked row</button></div>
        {doc.lines.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Item</th><th>Material / specification</th><th>Qty</th><th>Unit</th>
            <th>{schedules.length ? "Material unit rate" : isBaseline(doc) ? "Est. price" : "Unit price"}</th>
            {schedules.length > 0 && <><th>Material extension</th><th>Labor unit rate</th><th>Labor extension</th><th>Combined total</th></>}
            <th>Source</th><th>Edit</th></tr></thead>
          <tbody>{doc.lines.map((line) => {
            const f = line.fields;
            const anchor = line.anchors.material ?? line.anchors.description ?? Object.values(line.anchors)[0];
            const match = batch.matches[`${doc.id}:${line.id}`];
            const bomLine = bom?.lines.find((candidate) => candidate.id === match?.bom);
            return <tr key={line.id}><td className="mono">{f.code || line.id}</td>
              <td><strong>{f.material || f.description || "Unnamed row"}</strong>
                <small>{[line.kind === "service" ? "Service" : line.kind === "work" ? "Work item · material breakdown needed" : "", line.schedule, line.excluded ? "Excluded from verification" : "", f.specification, f.pack,
                  Object.values(line.anchors).some((item) => item.weak) ? "Source needs review" : ""].filter(Boolean).join(" · ")}</small>
                {f.material_quantity && <small>Separate material quantity · {f.material_quantity} {f.material_unit}</small>}
                {f.material_rate && <small>Separate material rate · {f.material_rate}</small>}
                {f.material_extension && <small>Separate material extension · {f.material_extension}</small>}
                {f.equipment_amount && <small>Equipment extension · {f.equipment_amount}</small>}
                {match && <small>Match {bomLine ? `· ${bomLine.fields.code || bomLine.id} · ${match.method}` : "needs review"}</small>}</td>
              <td className="mono">{f.quantity || "—"}</td><td>{f.unit || "—"}</td><td className="mono">{f.unit_price || "—"}</td>
              {schedules.length > 0 && ["amount", "labor_unit_price", "labor_amount", "line_total"].map((field) =>
                <td className="mono" key={field}>{f[field] || "—"}</td>)}
              <td>{anchor ? <span className="source-ref">{sourceName(anchor.source)}{anchor.cells?.length ? ` · ${anchor.cells.join(", ")}` : ""}</span> : "Unlinked"}</td>
              <td><button className="icon-button" type="button" disabled={historical} onClick={() => edit(line)}
                aria-label={`Edit ${f.material || f.description || line.id}`}><Pencil size={15} /></button></td></tr>;
          })}</tbody>
        </table></div> : doc.summary_only ? <Empty icon={<FileText size={22} />} title="Summary quotation">
          <p>Stated totals and terms are available. Item coverage, quantities and specifications are unverified.</p>
          <button className="button secondary" type="button" onClick={() => setMode("terms")}>View document terms</button>
        </Empty> : <Empty icon={<FileText size={22} />} title="No material rows found">
          <p>Check the original file and add any material rows you can verify.</p>
        </Empty>}
      </>}
      </>}
    </section>
    {scope && boms.length > 0 && <ScopeEditor key={batch.id} batch={batch} baseline={bom} choices={boms} close={() => setScope(false)} update={update} />}
    {type && canChangeType(doc) && <TypeEditor doc={doc} update={update} close={() => setType(false)} />}
    {editor && <FieldEditor key={typeof editor === "string" ? editor : editor.id} doc={doc} bom={bom}
      batch={batch} editor={editor} context={editorContext} update={update} close={() => { edit(null); setEditorContext(undefined); onEditorClose?.(); }} />}
  </div>;
}

function TypeEditor({ doc, update, close }: { doc: Document; update: (batch: Batch) => void; close: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [source, setSource] = useState<Anchor>();
  const uncertain = doc.role === "mixed" || doc.role === "unknown";
  const evidence = doc.classification?.evidence ?? [];
  const unnamed = doc.role === "quote" && supplierNameNeeded(doc.supplier);
  const title = unnamed ? "Add supplier name" : uncertain ? "Confirm document type" : "Change document type";

  return <Modal title={title} close={close} busy={busy} wide={Boolean(source)}>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      setBusy(true); setError("");
      try {
        update(await api<Batch>(`/documents/${doc.id}/role`, "PATCH", data));
        close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>{doc.name}</p>
      {doc.classification && <details className="notice" open={uncertain ? true : undefined}>
        <summary>{doc.intake === "override" ? "Original type assessment" : "Why this type?"}</summary>
        <p>{documentFeedback(doc.classification.basis)}</p>
        {evidence.slice(0, 4).map((anchor, index) =>
          <button className="source-excerpt" type="button" key={index} onClick={() => setSource(anchor)}>
            <strong>{sourceName(anchor.source)}</strong> {anchor.text}
          </button>)}
        {evidence.length > 4 && <details><summary>More excerpts</summary>
          {evidence.slice(4).map((anchor, index) =>
            <button className="source-excerpt" type="button" key={index} onClick={() => setSource(anchor)}>
              <strong>{sourceName(anchor.source)}</strong> {anchor.text}
            </button>)}
        </details>}
      </details>}
      {source && <SourceViewer key={evidence.indexOf(source)} doc={doc} initial={source.source} anchors={[source]} />}
      <label>Document type<select name="role" aria-label="Document type" required defaultValue={uncertain ? "" : doc.role}>
        {uncertain && <option value="" disabled>Choose a type</option>}
        {!doc.revises && !doc.bundle_parent && <>
          <option value="bom">Bill of materials</option>
          <option value="boq">Bill of quantities</option>
          <option value="buyer">Buyer requirements</option>
        </>}
        <option value="quote">Supplier quotation</option>
      </select></label>
      <label>Supplier name for quotations<input name="supplier" defaultValue={unnamed ? "" : doc.supplier || doc.terms.supplier} maxLength={120} /></label>
      {doc.role === "mixed" && doc.lines.length > 0 && <fieldset className="scope-checks">
        <legend>Exclude rows from reference sections</legend>
        {doc.lines.filter((line) => !line.excluded).map((line) => <label key={line.id} className="inline">
          <input type="checkbox" name="exclude_rows" value={line.id} />
          {line.id} · {line.fields.material || line.fields.description}
        </label>)}
      </fieldset>}
      <label>Reason for correction<input name="reason" maxLength={500} required /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <PendingButton type="submit" className="button primary" pending={busy} pendingLabel="Saving…">Save document type</PendingButton>
    </form>
  </Modal>;
}

function ScopeEditor({ batch, baseline, choices, update, close }: {
  batch: Batch; baseline?: Document; choices: Document[]; update: (value: Batch) => void; close: () => void;
}) {
  const [baselineId, setBaselineId] = useState(baseline?.id ?? "");
  const [mode, setMode] = useState(batch.scope_mode);
  const [rows, setRows] = useState(new Set(batch.scope));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const bom = choices.find((doc) => doc.id === baselineId);
  const materials = procurementRows(bom?.lines ?? []);
  const schedules = [...new Set(materials.map((line) => line.schedule ?? ""))];
  const selectedSchedule = materials.find((line) => rows.has(line.id))?.schedule ?? "";
  return <Modal title="Set purchase scope" close={close} busy={busy}>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      try {
        update(await api<Batch>(`/batches/${batch.id}/scope`, "PATCH",
          { mode, rows: [...rows], ...(choices.length > 1 ? { baseline: baselineId } : {}) }));
        close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>Choose the material lines suppliers were asked to quote for this purchase round.</p>
      {choices.length > 1 && <label>Purchase baseline<select required value={baselineId} onChange={(event) => {
        setBaselineId(event.target.value); setMode("unknown"); setRows(new Set());
      }}>
        <option value="" disabled>Choose a BOM or BOQ</option>
        {choices.map((doc) => <option key={doc.id} value={doc.id} disabled={!procurementRows(doc.lines).length}>
          {docName(doc)} · {doc.name}
        </option>)}
      </select></label>}
      <label>Scope<select aria-label="Scope" disabled={!bom} value={mode} onChange={(event) => setMode(event.target.value as Scope)}>
        <option value="unknown">Scope unknown</option><option value="all" disabled={schedules.length > 1}>All {bom?.role === "boq" ? "BOQ material" : "BOM"} items</option><option value="selected">Select individual items</option>
      </select></label>
      {schedules.length > 1 && <label>Supplier schedule<select value={selectedSchedule} onChange={(event) => {
        const chosen = event.target.value;
        setMode("selected");
        setRows(new Set(materials.filter((line) => (line.schedule ?? "") === chosen).map((line) => line.id)));
      }}>
        <option value="" disabled>Choose one schedule</option>
        {schedules.filter(Boolean).map((name) => <option key={name} value={name}>{name}</option>)}
      </select></label>}
      {mode === "selected" && <div className="scope-checks">{materials.filter((line) =>
        schedules.length <= 1 || (line.schedule ?? "") === selectedSchedule).map((line) => <label key={line.id} className="inline">
        <input type="checkbox" checked={rows.has(line.id)} onChange={(event) => {
          const next = new Set(rows);
          if (event.target.checked) next.add(line.id); else next.delete(line.id);
          setRows(next);
        }} /> {line.fields.code || line.id} · {line.fields.material || line.fields.description}
      </label>)}</div>}
      {error && <p className="error" role="alert">{error}</p>}
      <PendingButton className="button primary" type="submit" pending={busy} disabled={!bom} pendingLabel="Saving scope…">Save scope</PendingButton>
    </form>
  </Modal>;
}

function FieldEditor({ doc, bom, batch, editor, context, update, close }: {
  doc: Document; bom?: Document; batch: Batch; editor: Line | "terms" | "new"; context?: SourceEditor;
  update: (value: Batch) => void; close: () => void;
}) {
  const terms = editor === "terms";
  const line = typeof editor === "object" ? editor : undefined;
  const initial = terms ? doc.terms : line?.fields ?? {};
  const original = terms ? doc.term_anchors : line?.anchors ?? {};
  const fields = terms ? termFields : lineFields;
  const [values, setValues] = useState<Record<string, string>>(initial);
  const [linked, setLinked] = useState<Record<string, Link>>({});
  const [field, setField] = useState(context?.field ?? Object.keys(fields).find((key) => original[key]) ?? (terms ? "tax" : "material"));
  const onCurrentBaseline = !line?.match_baseline || line.match_baseline === bom?.id;
  const initialMatch = onCurrentBaseline ? line?.match ?? "auto" : "auto";
  const initialFactor = onCurrentBaseline ? line?.factor ?? "" : "";
  const [match, setMatch] = useState(initialMatch);
  const [factor, setFactor] = useState(initialFactor);
  const [note, setNote] = useState("");
  const [excluded, setExcluded] = useState(Boolean(line?.excluded));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const anchor: Anchor | undefined = original[field];
  const draft = linked[field];
  const matching = !terms && doc.role === "quote" && Boolean(bom);
  const currentMatch = line && batch.matches[`${doc.id}:${line.id}`];

  return <Modal title={terms ? "Edit document terms" : line ? "Edit extracted material" : "Add material with source evidence"}
    close={close} busy={busy || suggesting} wide>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setError(""); setBusy(true);
      const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (initial[key] ?? "")));
      const payload: Record<string, unknown> = { values: changed, anchors: linked, note };
      if (line && excluded !== Boolean(line.excluded)) payload.excluded = excluded;
      if (matching && match !== initialMatch) payload.match = match === "auto" ? null : match;
      if (matching && factor !== initialFactor) payload.factor = factor;
      const path = terms ? "/terms" : line ? `/line/${encodeURIComponent(line.id)}` : "/lines";
      try {
        update(await api<Batch>(`/documents/${doc.id}${path}`, !terms && !line ? "POST" : "PATCH", payload));
        close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <div className="editor-grid">
        <div className="editor-fields">
          {Object.entries(fields).map(([key, title]) => <div className="editor-field" key={key}>
            <label>{title}<input value={values[key] ?? ""} maxLength={500} onFocus={() => setField(key)}
              onChange={(event) => setValues({ ...values, [key]: event.target.value })} /></label>
            <button className={`icon-button ${field === key ? "active-link" : ""}`} type="button" title={`Link ${title} to source`}
              aria-label={`Link ${title} to source`} onClick={() => setField(key)}><Link2 size={16} /></button>
          </div>)}
          {line && <label className="inline">
            <input type="checkbox" checked={excluded} onChange={(event) => setExcluded(event.target.checked)} />
            Exclude this source row from verification
          </label>}
          {matching && <div className="stack">
            <label>Match to BOM<select value={match} onChange={(event) => setMatch(event.target.value)}>
              <option value="auto">Automatic{currentMatch?.bom ? ` · ${currentMatch.bom}` : ""}</option>
              <option value="">No BOM match</option>
              {procurementRows(bom?.lines ?? []).map((candidate) => <option value={candidate.id} key={candidate.id}>
                {candidate.fields.code || candidate.id} · {candidate.fields.material || candidate.fields.description}
              </option>)}
            </select></label>
            {currentMatch?.candidates.length ? <p className="fine-print">Candidates {currentMatch.candidates.join(", ")}</p> : null}
            {line && batch.ai.configured && <PendingButton className="button secondary" type="button"
              pending={suggesting} pendingLabel="Finding matches…" disabled={busy} onClick={async () => {
              setSuggesting(true);
              try {
                const result = await api<{ candidates: string[]; message: string }>(
                  `/documents/${doc.id}/line/${encodeURIComponent(line.id)}/suggest`, "POST");
                setSuggestion(`${documentFeedback(result.message)} ${result.candidates.join(", ")}`);
              } catch (error) { setSuggestion((error as Error).message); }
              finally { setSuggesting(false); }
            }}>Suggest matches</PendingButton>}
            {suggestion && <p className="notice" role="status">{suggestion}</p>}
            <label>BOM units per quoted unit<input value={factor} inputMode="decimal" placeholder="Only for a documented conversion"
              onChange={(event) => setFactor(event.target.value)} /></label>
          </div>}
        </div>
        <section className="editor-source">
          <div className="evidence-heading"><div><h3>{fields[field]}</h3>
            <p>{doc.kind === "pdf" ? "Drag over the source text to link a region." : "Select a populated cell to link it."}</p></div>
          </div>
          {anchor?.quality_reason && <p className="notice">{anchor.quality_reason}</p>}
          <SourceViewer key={field} doc={doc} initial={draft?.source ?? (field === context?.field ? context.source : undefined) ?? anchor?.source} anchors={anchor ? [anchor] : []}
            draft={draft} onLink={(link) => setLinked({ ...linked, [field]: link })} />
          {draft?.box && <label>Original text in marked region<input maxLength={500}
            value={draft.text ?? ""} onChange={(event) => setLinked({ ...linked, [field]: { ...draft, text: event.target.value } })} /></label>}
          {draft?.cell && <p className="notice">Linked to {sourceName(draft.source)} · {draft.cell}</p>}
        </section>
      </div>
      <label>Correction note<input value={note} maxLength={500} placeholder="Required for a packaging conversion"
        onChange={(event) => setNote(event.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <PendingButton type="submit" className="button primary" pending={busy} disabled={suggesting}
        pendingLabel="Saving correction…">Save correction</PendingButton>
    </form>
  </Modal>;
}
