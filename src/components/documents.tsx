"use client";

import { FileText, Link2, Pencil, Plus, RotateCcw, UploadCloud } from "lucide-react";
import { useState } from "react";
import { api, coverageLabel, docName, documentFeedback, lineFields, procurementRows, sourceName, supplierNameNeeded, termFields } from "./model";
import type { Anchor, Batch, Coverage, Document, Line, Scope, SourceEditor } from "./model";
import { Empty, LoadingNotice, Modal, PendingButton, Pill, Spinner } from "./shared";
import { SourceViewer } from "./viewer";
import type { Link } from "./viewer";

const statusLabel = { ready: "Ready", processing: "Reading document", needs_review: "Needs review", failed: "Failed" };

export type DocumentDestination = {
  document: string; kind: "type" | "scope" | "retry" | "read" | "edit"; editor?: SourceEditor;
};

export function canChangeType(doc: Document) {
  return (doc.status === "ready" || doc.status === "needs_review") && doc.role !== "unsupported" && !doc.superseded_by && !doc.classification?.bundled_offers?.length;
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
  const [scope, setScope] = useState(destination?.kind === "scope");
  const [type, setType] = useState(destination?.kind === "type");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"coverage" | "retry" | null>(null);
  const boms = batch.documents.filter((item) => item.role === "bom" && !item.superseded_by);
  const bom = boms.length === 1 ? boms[0] : undefined;
  const revisedFrom = batch.documents.find((item) => item.id === doc?.revises);
  const revisedTo = batch.documents.find((item) => item.id === doc?.superseded_by);
  const pendingRevision = batch.documents.some((item) => item.revises === doc?.id);
  const count = batch.scope_mode === "all" ? procurementRows(bom?.lines ?? []).length : batch.scope.length;

  if (!doc) return <Empty icon={<UploadCloud size={27} />} title="Upload your first source">
    <p>Add a bill of materials and up to five supplier quotations together in PDF, XLSX, or DOCX format.</p>
    <button className="button primary" type="button" onClick={upload}><Plus size={16} /> Upload documents</button>
  </Empty>;

  const scheduleSources = doc.sources.filter((source) => source.schedule);
  const schedules = [...new Set(scheduleSources.map((source) => source.schedule))];
  const materialCount = doc.lines.filter((line) => line.kind !== "service").length;

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
      {batch.documents.map((item) => <button key={item.id} className={`document-choice ${doc.id === item.id ? "selected" : ""}`}
        type="button" aria-current={doc.id === item.id ? "true" : undefined}
        onClick={() => { select(item.id); setMode("extraction"); setError(""); setEditorContext(undefined); }}>
        <span className={`file-glyph ${item.role}`}><FileText size={17} /></span>
        <span><strong>{docName(item)}</strong><small>{item.name}</small><span className="document-status">{item.status === "processing" && <Spinner />}{item.superseded_by ? "Superseded" : statusLabel[item.status]}</span></span>
      </button>)}
      <button className="button secondary full" type="button" onClick={upload}><Plus size={15} /> Add document</button>
    </aside>
    <section className="document-detail">
      <div className="detail-heading">
        <div><div className="inline"><h2>{docName(doc)}</h2>
          {doc.role === "quote" && <Pill value="Supplier quotation" />}
          {doc.status !== "processing" && <Pill value={revisedTo ? "Superseded" : statusLabel[doc.status]} />}</div>
          <p>{doc.name} · {doc.sources.length} {doc.kind === "pdf" ? "pages" : doc.kind === "docx" ? "sections" : "worksheets"}</p>
        </div>
        <button className="button secondary" type="button" onClick={() => manage(doc)}>Manage file</button>
      </div>
      <div className="detail-settings">
        {doc.role === "quote" && canChangeType(doc) && supplierNameNeeded(doc.supplier) &&
          <button className="button secondary" type="button" onClick={() => setType(true)}>Add supplier name</button>}
        {doc.role === "bom" ? <div className="inline">
          <span className="muted">Purchase scope</span><Pill value={batch.scope_mode === "unknown" ? "Unset" : `${count} selected`} />
          <button className="button secondary" type="button" disabled={!bom || !procurementRows(bom.lines).length || bom.status === "processing"}
            onClick={() => setScope(true)}>Set scope</button>
        </div> : doc.role === "quote" && !revisedTo ? <label className="inline">Supplier coverage<select disabled={busy !== null} aria-busy={busy === "coverage"} value={doc.coverage}
          onChange={(event) => void changeCoverage(event.target.value as Coverage)}>
          {Object.entries(coverageLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select>{busy === "coverage" && <span role="status" className="inline"><Spinner />Saving coverage…</span>}</label> : null}
        {doc.role === "quote" && !pendingRevision && doc.status !== "processing" &&
          <button className="button secondary" type="button" onClick={() => revise(doc)}>Upload revision</button>}
        {(doc.status === "failed" || doc.status === "needs_review" || doc.parser_update_available) &&
          <PendingButton className="button secondary" type="button" disabled={busy !== null} pending={busy === "retry"}
            pendingLabel="Starting…" autoFocus={destination?.kind === "retry" && doc.id === destination.document} icon={<RotateCcw size={15} />} onClick={() => void retryExtraction()}>
            {doc.parser_update_available ? "Read PDF again" : "Try reading again"}
          </PendingButton>}

      </div>
      {revisedFrom && <p className="notice">Revision of {revisedFrom.name}.</p>}
      {revisedTo && <p className="notice">Superseded by {revisedTo.name}.</p>}
      {Boolean(doc.classification?.bundled_offers?.length) && <div className="notice stack">
        <strong>Separate supplier offers</strong>
        <p>Download each offer, remove this bundle, and upload the separate files to compare them.</p>
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
      {schedules.length > 0 && <details className="notice">
        <summary>{schedules.length} priced {schedules.length === 1 ? "schedule" : "schedules"} · {materialCount} material rows · {doc.lines.length - materialCount} service rows</summary>
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
      {doc.status === "processing" ? <LoadingNotice className="document-progress">Reading {doc.name}…</LoadingNotice> : <>
      <nav className="segment" aria-label="Document view">
        {([["extraction", "Material lines"], ["terms", "Document terms"], ["source", "Original source"]] as const).map(([value, label]) =>
          <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}
      </nav>
      {mode === "source" ? <div className="source-single">
        <SourceViewer key={doc.id} doc={doc} />
      </div> : mode === "terms" ? <>
        <div className="table-heading"><h3>Extracted terms</h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length}
            onClick={() => edit("terms")}><Pencil size={15} /> Edit terms</button></div>
        {Object.keys(doc.terms).length ? <dl className="terms-grid">
          {Object.entries(doc.terms).map(([field, value]) => <div key={field}><dt>{termFields[field] ?? field}</dt><dd>{value || "—"}</dd></div>)}
        </dl> : <p className="notice">No labeled terms were extracted. Inspect the source to enter terms.</p>}
      </> : <>
        <div className="table-heading"><h3>{schedules.length ? "Extracted schedule rows" : "Extracted materials"} <span>{doc.lines.length}</span></h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length}
            onClick={() => edit("new")}><Plus size={15} /> Add linked row</button></div>
        {doc.lines.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Item</th><th>Material / specification</th><th>Qty</th><th>Unit</th>
            <th>{schedules.length ? "Material unit rate" : doc.role === "bom" ? "Est. price" : "Unit price"}</th>
            {schedules.length > 0 && <><th>Material extension</th><th>Labor unit rate</th><th>Labor extension</th><th>Combined total</th></>}
            <th>Source</th><th>Edit</th></tr></thead>
          <tbody>{doc.lines.map((line) => {
            const f = line.fields;
            const anchor = line.anchors.material ?? line.anchors.description ?? Object.values(line.anchors)[0];
            const match = batch.matches[`${doc.id}:${line.id}`];
            const bomLine = bom?.lines.find((candidate) => candidate.id === match?.bom);
            return <tr key={line.id}><td className="mono">{f.code || line.id}</td>
              <td><strong>{f.material || f.description || "Unnamed row"}</strong>
                <small>{[line.kind === "service" ? "Service" : "", line.schedule, line.excluded ? "Excluded from verification" : "", f.specification, f.pack,
                  Object.values(line.anchors).some((item) => item.weak) ? "Source needs review" : ""].filter(Boolean).join(" · ")}</small>
                {match && <small>Match {bomLine ? `· ${bomLine.fields.code || bomLine.id} · ${match.method}` : "needs review"}</small>}</td>
              <td className="mono">{f.quantity || "—"}</td><td>{f.unit || "—"}</td><td className="mono">{f.unit_price || "—"}</td>
              {schedules.length > 0 && ["amount", "labor_unit_price", "labor_amount", "line_total"].map((field) =>
                <td className="mono" key={field}>{f[field] || "—"}</td>)}
              <td>{anchor ? <span className="source-ref">{sourceName(anchor.source)}{anchor.cells?.length ? ` · ${anchor.cells.join(", ")}` : ""}</span> : "Unlinked"}</td>
              <td><button className="icon-button" type="button" onClick={() => edit(line)}
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
    {scope && bom && <ScopeEditor key={batch.id} batch={batch} bom={bom} close={() => setScope(false)} update={update} />}
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
        {!doc.revises && <option value="bom">Bill of materials</option>}
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

function ScopeEditor({ batch, bom, update, close }: {
  batch: Batch; bom: Document; update: (value: Batch) => void; close: () => void;
}) {
  const [mode, setMode] = useState(batch.scope_mode);
  const [rows, setRows] = useState(new Set(batch.scope));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const materials = procurementRows(bom.lines);
  const schedules = [...new Set(materials.map((line) => line.schedule ?? ""))];
  const selectedSchedule = materials.find((line) => rows.has(line.id))?.schedule ?? "";
  return <Modal title="Set purchase scope" close={close} busy={busy}>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      try {
        update(await api<Batch>(`/batches/${batch.id}/scope`, "PATCH", { mode, rows: [...rows] }));
        close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>Choose the BOM lines suppliers were asked to quote for this purchase round.</p>
      <label>Scope<select aria-label="Scope" value={mode} onChange={(event) => setMode(event.target.value as Scope)}>
        <option value="unknown">Scope unknown</option><option value="all" disabled={schedules.length > 1}>All BOM items</option><option value="selected">Select individual items</option>
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
      <PendingButton className="button primary" type="submit" pending={busy} pendingLabel="Saving scope…">Save scope</PendingButton>
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
  const [match, setMatch] = useState(line?.match ?? "auto");
  const [factor, setFactor] = useState(line?.factor ?? "");
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
      if (matching && match !== (line?.match ?? "auto")) payload.match = match === "auto" ? null : match;
      if (matching && factor !== (line?.factor ?? "")) payload.factor = factor;
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
