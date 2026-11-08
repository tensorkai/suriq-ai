"use client";

import { Check, ClipboardList, LocateFixed, ScanLine, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { api, docName, documentFeedback, reviews, sourceName } from "./model";
import type { Anchor, Batch, Issue, ReviewStatus } from "./model";
import { Empty, PendingButton, Pill } from "./shared";
import { SourceViewer, Zoom } from "./viewer";

export function Findings({ batch, update }: { batch: Batch; update: (batch: Batch) => void }) {
  const [active, setActive] = useState("");
  const [filter, setFilter] = useState("all");
  const [zoom, setZoom] = useState(100);
  const [focus, setFocus] = useState(0);
  const items = batch.issues.filter((item) => filter === "all" || item.severity === filter);
  const issue = items.find((item) => item.id === active) ?? items[0];

  if (!batch.verified_at || batch.stale) return <Empty icon={<ScanLine size={24} />} title="Ready when your sources are">
    <p>Upload a BOM and supplier quotations, choose purchase scope, then run verification.</p>
  </Empty>;
  if (!batch.issues.length) return <Empty icon={<ShieldCheck size={25} />} title="No findings in this verification">
    <p>Review extraction coverage and supplier terms before using the comparison.</p>
  </Empty>;

  const docs = new Map(batch.documents.map((doc) => [doc.id, doc]));
  const pages = new Map<string, { document: string; source: number | string; anchors: Anchor[] }>();
  for (const anchor of issue?.evidence ?? []) {
    const key = `${anchor.document}:${anchor.source}`;
    const group = pages.get(key) ?? { document: anchor.document, source: anchor.source, anchors: [] };
    group.anchors.push(anchor);
    pages.set(key, group);
  }

  return <div className="findings-layout">
    <aside className="issue-rail">
      <div className="issue-rail-heading"><span>Findings</span><span>{items.length}</span></div>
      <label className="sr-only" htmlFor="severity-filter">Filter by severity</label>
      <select id="severity-filter" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">All severities</option><option>Blocking</option><option>Warning</option><option>Info</option>
      </select>
      <div role="tablist" aria-label="Discrepancies" aria-orientation="vertical" className="issue-tabs">
        {items.map((item, index) => <button role="tab" type="button" key={item.id} id={`issue-${item.id}`}
          aria-selected={issue?.id === item.id} aria-controls="issue-content" tabIndex={issue?.id === item.id ? 0 : -1}
          onClick={() => setActive(item.id)} onKeyDown={(event) => {
            let target: number;
            switch (event.key) {
              case "ArrowDown": target = (index + 1) % items.length; break;
              case "ArrowUp": target = (index - 1 + items.length) % items.length; break;
              case "Home": target = 0; break;
              case "End": target = items.length - 1; break;
              default: return;
            }
            event.preventDefault();
            setActive(items[target].id);
            document.getElementById(`issue-${items[target].id}`)?.focus();
          }}>
          <span className={`severity-dot ${item.severity.toLowerCase()}`} />
          <span><strong>{item.title}</strong><small>{item.severity} · {reviews[item.review.status]}</small></span>
        </button>)}
      </div>
    </aside>
    {issue ? <section className="issue-content" id="issue-content" role="tabpanel" aria-labelledby={`issue-${issue.id}`}>
      <div className="issue-summary">
        <div className="inline"><Pill value={issue.severity} /><Pill value={reviews[issue.review.status]} />
          {issue.review_required && <Pill value="Needs review" />}</div>
        <h2>{issue.title}</h2><p>{documentFeedback(issue.note)}</p>
        <ReviewForm key={`${issue.id}:${issue.fingerprint}:${issue.review.status}:${issue.review.note}`}
          batch={batch.id} issue={issue} update={update} />
      </div>
      <div className="evidence-heading"><div><h3>Source evidence</h3><p>{pages.size} linked {pages.size === 1 ? "location" : "locations"}</p></div>
        <div className="inline">{pages.size > 0 && <button className="button secondary" type="button" onClick={() => {
          setZoom((value) => Math.max(value, 150)); setFocus((value) => value + 1);
        }}><LocateFixed size={15} /> Focus highlights</button>}<Zoom value={zoom} change={setZoom} /></div></div>
      {pages.size ? <div className="evidence-grid">
        {[...pages.entries()].map(([key, entry]) => {
          const doc = docs.get(entry.document);
          return doc ? <SourceViewer key={`${issue.id}:${key}`} doc={doc} initial={entry.source} anchors={entry.anchors}
            zoom={zoom} focus={focus} /> : null;
        })}
      </div> : <Empty icon={<ClipboardList size={21} />} title="Source anchor required">
        <p>Open the extraction grid and link the affected field to its original page or cell.</p>
      </Empty>}
      {pages.size > 0 && <p className="fine-print">{[...pages.values()].map((entry) => {
        const doc = docs.get(entry.document);
        return doc ? `${docName(doc)} · ${sourceName(entry.source)}` : null;
      }).filter(Boolean).join(" / ")}</p>}
    </section> : <Empty icon={<ClipboardList size={22} />} title="No findings at this severity" />}
  </div>;
}

function ReviewForm({ batch, issue, update }: { batch: string; issue: Issue; update: (batch: Batch) => void }) {
  const [status, setStatus] = useState(issue.review.status);
  const [note, setNote] = useState(issue.review.note);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  return <form className="review-form" onSubmit={async (event) => {
    event.preventDefault(); setBusy(true); setError(""); setSaved(false);
    try {
      update(await api<Batch>(`/batches/${batch}/issues/${issue.id}`, "PATCH", { status, note, fingerprint: issue.fingerprint }));
      setSaved(true);
    } catch (error) {
      setError((error as Error).message);
    } finally { setBusy(false); }
  }}>
    <label>Review status<select aria-label="Review status" value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus)}>
      {Object.entries(reviews).map(([key, label]) => <option key={key} value={key}
        disabled={key === "confirmed" && !issue.confirmable}>{label}</option>)}
    </select></label>
    <label className="grow">Internal note<input placeholder="Optional review note" value={note} maxLength={500}
      onChange={(event) => setNote(event.target.value)} /></label>
    <PendingButton className="button secondary" type="submit" pending={busy} pendingLabel="Saving review…"
      icon={<Check size={15} />}>Save review</PendingButton>
    {error && <p className="error" role="alert">{error}</p>}
    {saved && <span role="status" className="fine-print">Review saved.</span>}
  </form>;
}
