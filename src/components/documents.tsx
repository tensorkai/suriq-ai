"use client";

import { FileText, Link2, Pencil, Plus, RotateCcw, UploadCloud } from "lucide-react";
import { useState } from "react";
import { api, docName, lineFields, sourceName, termFields } from "./model";
import type { Anchor, Batch, Coverage, Document, Line, Scope } from "./model";
import { Empty, Modal, Pill } from "./shared";
import { SourceViewer, Zoom } from "./viewer";
import type { Link } from "./viewer";

const statusLabel = { ready: "Ready", processing: "Processing", needs_review: "Needs review", failed: "Failed" };
const coverageLabel = { full: "Full scope", partial: "Partial offer", unknown: "Coverage unknown" };

export function Documents({ batch, update, upload, manage }: {
  batch: Batch; update: (value: Batch) => void; upload: () => void; manage: (doc: Document) => void;
}) {
  const [selected, select] = useState("");
  const [mode, setMode] = useState<"extraction" | "source" | "terms">("extraction");
  const [editor, edit] = useState<Line | "terms" | "new" | null>(null);
  const [zoom, setZoom] = useState(100);
  const [scope, setScope] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const doc = batch.documents.find((item) => item.id === selected) ?? batch.documents[0];
  const bom = batch.documents.find((item) => item.role === "bom");
  const count = batch.scope_mode === "all" ? bom?.lines.length ?? 0 : batch.scope.length;

  if (!doc) return <Empty icon={<UploadCloud size={27} />} title="Upload your first source">
    <p>Start with a bill of materials. Then add up to five supplier quotations in PDF or XLSX format.</p>
    <button className="button primary" type="button" onClick={upload}><Plus size={16} /> Upload documents</button>
  </Empty>;

  async function changeCoverage(coverage: Coverage) {
    setBusy(true); setError("");
    try {
      update(await api<Batch>(`/documents/${doc.id}/coverage`, "PATCH", { coverage }));
    } catch (error) {
      setError((error as Error).message);
    } finally { setBusy(false); }
  }

  return <div className="documents-layout">
    <aside className="document-rail">
      <div className="issue-rail-heading"><span>SOURCE FILES</span><span>{batch.documents.length}</span></div>
      {batch.documents.map((item) => <button key={item.id} className={`document-choice ${doc.id === item.id ? "selected" : ""}`}
        type="button" aria-current={doc.id === item.id ? "true" : undefined}
        onClick={() => { select(item.id); setMode("extraction"); setError(""); }}>
        <span className={`file-glyph ${item.role}`}><FileText size={17} /></span>
        <span><strong>{docName(item)}</strong><small>{item.name}</small><span className="document-status">{statusLabel[item.status]}</span></span>
      </button>)}
      <button className="button secondary full" type="button" onClick={upload}><Plus size={15} /> Add document</button>
    </aside>
    <section className="document-detail">
      <div className="detail-heading">
        <div><div className="inline"><span className="eyebrow">{doc.role === "bom" ? "REQUIREMENTS" : "SUPPLIER OFFER"}</span>
          <Pill value={statusLabel[doc.status]} /></div>
          <h2>{docName(doc)}</h2><p>{doc.name} · {doc.sources.length} {doc.kind === "pdf" ? "pages" : "worksheets"}</p>
        </div>
        <button className="button secondary" type="button" onClick={() => manage(doc)}>Manage file</button>
      </div>
      <div className="detail-settings">
        {doc.role === "bom" ? <div className="inline">
          <span className="muted">Purchase scope</span><Pill value={batch.scope_mode === "unknown" ? "Unset" : `${count} selected`} />
          <button className="button secondary" type="button" disabled={!bom?.lines.length} onClick={() => setScope(true)}>Set scope</button>
        </div> : <label className="inline">Supplier coverage<select disabled={busy} value={doc.coverage}
          onChange={(event) => void changeCoverage(event.target.value as Coverage)}>
          {Object.entries(coverageLabel).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        </select></label>}
        {(doc.status === "failed" || doc.status === "needs_review") && <button className="button secondary" type="button"
          disabled={busy} onClick={async () => {
            setBusy(true); setError("");
            try {
              await api(`/documents/${doc.id}/retry`, "POST");
              update(await api<Batch>(`/batches/${batch.id}`));
            } catch (error) { setError((error as Error).message); }
            finally { setBusy(false); }
          }}><RotateCcw size={15} /> Retry extraction</button>}
      </div>
      {doc.error && <p className="notice" role="status">{doc.error}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <nav className="segment" aria-label="Document view">
        {([["extraction", "Material lines"], ["terms", "Document terms"], ["source", "Original source"]] as const).map(([value, label]) =>
          <button key={value} type="button" aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}
      </nav>
      {mode === "source" ? <div className="source-single">
        <div className="source-controls"><Zoom value={zoom} change={setZoom} /></div>
        <SourceViewer key={doc.id} doc={doc} zoom={zoom} />
      </div> : mode === "terms" ? <>
        <div className="table-heading"><h3>Extracted terms</h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length}
            onClick={() => edit("terms")}><Pencil size={15} /> Edit terms</button></div>
        {Object.keys(doc.terms).length ? <dl className="terms-grid">
          {Object.entries(doc.terms).map(([field, value]) => <div key={field}><dt>{termFields[field] ?? field}</dt><dd>{value || "—"}</dd></div>)}
        </dl> : <p className="notice">No labeled terms were extracted. Inspect the source to enter terms.</p>}
      </> : <>
        <div className="table-heading"><h3>Extracted materials <span>{doc.lines.length}</span></h3>
          <button className="button secondary" type="button" disabled={!doc.sources.length}
            onClick={() => edit("new")}><Plus size={15} /> Add linked row</button></div>
        {doc.lines.length ? <div className="table-scroll"><table className="data-table">
          <thead><tr><th>Item</th><th>Material / specification</th><th>Qty</th><th>Unit</th>
            <th>{doc.role === "bom" ? "Est. price" : "Unit price"}</th><th>Source</th><th>Edit</th></tr></thead>
          <tbody>{doc.lines.map((line) => {
            const f = line.fields;
            const anchor = line.anchors.material ?? line.anchors.description ?? Object.values(line.anchors)[0];
            const match = batch.matches[`${doc.id}:${line.id}`];
            const bomLine = bom?.lines.find((candidate) => candidate.id === match?.bom);
            return <tr key={line.id}><td className="mono">{f.code || line.id}</td>
              <td><strong>{f.material || f.description || "Unnamed row"}</strong>
                <small>{[f.specification, f.pack].filter(Boolean).join(" · ")}</small>
                {match && <small>Match {bomLine ? `· ${bomLine.fields.code || bomLine.id} · ${match.method}` : "needs review"}</small>}</td>
              <td className="mono">{f.quantity || "—"}</td><td>{f.unit || "—"}</td><td className="mono">{f.unit_price || "—"}</td>
              <td>{anchor ? <span className="source-ref">{sourceName(anchor.source)}{anchor.cells?.length ? ` · ${anchor.cells.join(", ")}` : ""}</span> : "Unlinked"}</td>
              <td><button className="icon-button" type="button" onClick={() => edit(line)}
                aria-label={`Edit ${f.material || f.description || line.id}`}><Pencil size={15} /></button></td></tr>;
          })}</tbody>
        </table></div> : <Empty icon={<FileText size={22} />} title={doc.status === "processing" ? "Extracting source" : "No material rows yet"}>
          <p>{doc.status === "processing" ? "This view refreshes as extraction completes." : "Open the original source and add linked material rows where needed."}</p>
        </Empty>}
      </>}
    </section>
    {scope && bom && <ScopeEditor key={batch.id} batch={batch} bom={bom} close={() => setScope(false)} update={update} />}
    {editor && <FieldEditor key={typeof editor === "string" ? editor : editor.id} doc={doc} bom={bom}
      batch={batch} editor={editor} update={update} close={() => edit(null)} />}
  </div>;
}

function ScopeEditor({ batch, bom, update, close }: {
  batch: Batch; bom: Document; update: (value: Batch) => void; close: () => void;
}) {
  const [mode, setMode] = useState(batch.scope_mode);
  const [rows, setRows] = useState(new Set(batch.scope));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <Modal title="Set purchase scope" close={close}>
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
        <option value="unknown">Scope unknown</option><option value="all">All BOM items</option><option value="selected">Select individual items</option>
      </select></label>
      {mode === "selected" && <div className="scope-checks">{bom.lines.map((line) => <label key={line.id} className="inline">
        <input type="checkbox" checked={rows.has(line.id)} onChange={(event) => {
          const next = new Set(rows);
          if (event.target.checked) next.add(line.id); else next.delete(line.id);
          setRows(next);
        }} /> {line.fields.code || line.id} · {line.fields.material || line.fields.description}
      </label>)}</div>}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" type="submit" disabled={busy}>Save scope</button>
    </form>
  </Modal>;
}

function FieldEditor({ doc, bom, batch, editor, update, close }: {
  doc: Document; bom?: Document; batch: Batch; editor: Line | "terms" | "new";
  update: (value: Batch) => void; close: () => void;
}) {
  const terms = editor === "terms";
  const line = typeof editor === "object" ? editor : undefined;
  const initial = terms ? doc.terms : line?.fields ?? {};
  const original = terms ? doc.term_anchors : line?.anchors ?? {};
  const fields = terms ? termFields : lineFields;
  const [values, setValues] = useState<Record<string, string>>(initial);
  const [linked, setLinked] = useState<Record<string, Link>>({});
  const [field, setField] = useState(Object.keys(fields).find((key) => original[key]) ?? (terms ? "tax" : "material"));
  const [match, setMatch] = useState(line?.match ?? "auto");
  const [factor, setFactor] = useState(line?.factor ?? "");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState("");
  const [suggesting, setSuggesting] = useState(false);
  const [zoom, setZoom] = useState(100);
  const anchor: Anchor | undefined = original[field];
  const draft = linked[field];
  const matching = !terms && doc.role === "quote" && Boolean(bom);
  const currentMatch = line && batch.matches[`${doc.id}:${line.id}`];

  return <Modal title={terms ? "Edit document terms" : line ? "Edit extracted material" : "Add material with source evidence"}
    close={close} wide>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setError(""); setBusy(true);
      const changed = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (initial[key] ?? "")));
      const payload: Record<string, unknown> = { values: changed, anchors: linked, note };
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
          {matching && <div className="stack">
            <label>Match to BOM<select value={match} onChange={(event) => setMatch(event.target.value)}>
              <option value="auto">Automatic{currentMatch?.bom ? ` · ${currentMatch.bom}` : ""}</option>
              <option value="">No BOM match</option>
              {bom?.lines.map((candidate) => <option value={candidate.id} key={candidate.id}>
                {candidate.fields.code || candidate.id} · {candidate.fields.material || candidate.fields.description}
              </option>)}
            </select></label>
            {currentMatch?.candidates.length ? <p className="fine-print">Candidates {currentMatch.candidates.join(", ")}</p> : null}
            {line && <button className="button secondary" type="button" disabled={suggesting} onClick={async () => {
              setSuggesting(true);
              try {
                const result = await api<{ candidates: string[]; message: string }>(
                  `/documents/${doc.id}/line/${encodeURIComponent(line.id)}/suggest`, "POST");
                setSuggestion(`${result.message} ${result.candidates.join(", ")}`);
              } catch (error) { setSuggestion((error as Error).message); }
              finally { setSuggesting(false); }
            }}>Suggest matches with local AI</button>}
            {suggestion && <p className="notice" role="status">{suggestion}</p>}
            <label>BOM units per quoted unit<input value={factor} inputMode="decimal" placeholder="Only for a documented conversion"
              onChange={(event) => setFactor(event.target.value)} /></label>
          </div>}
        </div>
        <section className="editor-source">
          <div className="evidence-heading"><div><h3>{fields[field]}</h3>
            <p>{doc.kind === "pdf" ? "Drag over the source text to link a region." : "Select a populated cell to link it."}</p></div>
            <Zoom value={zoom} change={setZoom} />
          </div>
          <SourceViewer key={field} doc={doc} initial={draft?.source ?? anchor?.source} anchors={anchor ? [anchor] : []} zoom={zoom}
            draft={draft} onLink={(link) => setLinked({ ...linked, [field]: link })} />
          {draft?.box && <label>Original text in marked region<input maxLength={500}
            value={draft.text ?? ""} onChange={(event) => setLinked({ ...linked, [field]: { ...draft, text: event.target.value } })} /></label>}
          {draft?.cell && <p className="notice">Linked to {sourceName(draft.source)} · {draft.cell}</p>}
        </section>
      </div>
      <label>Correction note<input value={note} maxLength={500} placeholder="Required for a packaging conversion"
        onChange={(event) => setNote(event.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="button primary" disabled={busy}>Save correction</button>
    </form>
  </Modal>;
}
