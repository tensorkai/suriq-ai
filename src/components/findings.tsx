"use client";

import { Check, ClipboardList, LocateFixed, ScanLine, ShieldCheck } from "lucide-react";
import { useId, useState } from "react";
import { api, docName, documentFeedback, hasPreciseSource, lineFields, reviews, termFields } from "./model";
import type { Anchor, Batch, Issue, ReviewStatus, SourceEditor } from "./model";
import { Empty, PendingButton, Pill } from "./shared";
import { SourceViewer } from "./viewer";

const severity = { Blocking: 0, Warning: 1, Info: 2 };
const noteLimit = 500;

function needsReview(issue: Issue) {
  return issue.review.status === "open" || issue.review.status === "clarification";
}

export function Findings({ batch, update, initial, editSource }: {
  batch: Batch; update: (batch: Batch) => void; initial: string; editSource: (editor: SourceEditor, issueId: string) => void;
}) {
  const [active, setActive] = useState(initial);
  const [reviewFilter, setReviewFilter] = useState(() =>
    batch.issues.some((issue) => issue.id === initial && !needsReview(issue)) ? "all" : "pending"
  );
  const [filter, setFilter] = useState("all");
  const [focus, setFocus] = useState(0);
  const [saved, setSaved] = useState(false);
  const items = batch.issues.filter((item) => (reviewFilter === "all" || needsReview(item)) &&
    (filter === "all" || item.severity === filter)).sort((left, right) => severity[left.severity] - severity[right.severity]);
  const issue = items.find((item) => item.id === active) ?? items[0];
  const next = issue && [...items.slice(items.indexOf(issue) + 1), ...items.slice(0, items.indexOf(issue))]
    .find(needsReview);

  if (!batch.verified_at || batch.stale) return <Empty icon={<ScanLine size={24} />} title="Ready when your sources are">
    <p>Upload a BOM and supplier quotations, choose purchase scope, then run verification.</p>
  </Empty>;
  if (!batch.issues.length) return <Empty icon={<ShieldCheck size={25} />} title="No findings in this verification">
    <p>Review extraction coverage and supplier terms before using the comparison.</p>
  </Empty>;

  const docs = new Map(batch.documents.map((doc) => [doc.id, doc]));
  const pages = new Map<string, { document: string; source?: number | string; anchors: Anchor[] }>();
  const preciseDocuments = new Set(issue?.evidence.filter(hasPreciseSource).map((anchor) => anchor.document));
  for (const anchor of issue?.evidence ?? []) {
    const source = anchor.scope === "document" ? undefined : anchor.source;
    if (source === undefined && preciseDocuments.has(anchor.document)) continue;
    const key = `${anchor.document}:${source ?? "document"}`;
    const group = pages.get(key) ?? { document: anchor.document, source, anchors: [] };
    group.anchors.push(anchor);
    pages.set(key, group);
  }
  const editors = issue?.source_editors ?? [];
  const hasHighlights = Boolean(preciseDocuments.size);

  return <div className="findings-layout">
    <aside className="issue-rail">
      <div className="issue-rail-heading"><span>Findings</span><span>{items.length}</span></div>
      <label className="sr-only" htmlFor="review-filter">Review filter</label>
      <select id="review-filter" value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}>
        <option value="pending">Needs review</option><option value="all">All findings</option>
      </select>
      <label className="sr-only" htmlFor="severity-filter">Filter by severity</label>
      <select id="severity-filter" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">All severities</option><option>Blocking</option><option>Warning</option><option>Info</option>
      </select>
      <div role="tablist" aria-label="Discrepancies" aria-orientation="vertical" className="issue-tabs">
        {items.map((item, index) => <button role="tab" type="button" key={item.id} id={`issue-${item.id}`}
          aria-selected={issue?.id === item.id} aria-controls="issue-content" tabIndex={issue?.id === item.id ? 0 : -1}
          onClick={() => { setActive(item.id); setSaved(false); }} onKeyDown={(event) => {
            let target: number;
            switch (event.key) {
              case "ArrowDown": target = (index + 1) % items.length; break;
              case "ArrowUp": target = (index - 1 + items.length) % items.length; break;
              case "Home": target = 0; break;
              case "End": target = items.length - 1; break;
              default: return;
            }
            event.preventDefault();
            setActive(items[target].id); setSaved(false);
            document.getElementById(`issue-${items[target].id}`)?.focus();
          }}>
          <span className={`severity-dot ${item.severity.toLowerCase()}`} />
          <span><strong>{item.title}</strong>
            {item.detail?.context && <small className="issue-context">{item.detail.context}</small>}
            <small>{item.severity} · {reviews[item.review.status]}</small>
          </span>
        </button>)}
      </div>
    </aside>
    {issue ? <section className="issue-content" id="issue-content" role="tabpanel" aria-labelledby={`issue-${issue.id}`}>
      <div className="issue-summary">
        <div className="inline"><Pill value={issue.severity} /><Pill value={reviews[issue.review.status]} />
          {issue.review_required && <Pill value="Check source" />}</div>
        <h2>{issue.title}</h2>
        {issue.detail?.context && <p className="issue-context">{issue.detail.context}</p>}
        {issue.detail ? <dl className="finding-values">
          <div><dt>Expected or required</dt><dd>{issue.detail.expected || "Unestablished"}</dd></div>
          <div><dt>Offered or observed</dt><dd>{issue.detail.observed || "Unestablished"}</dd></div>
          <div className="full"><dt>Difference or unresolved reason</dt><dd>{documentFeedback(issue.detail.reason)}</dd></div>
          <div className="full"><dt>Action</dt><dd>{issue.detail.action}</dd></div>
        </dl> : <p>{documentFeedback(issue.note)}</p>}
        {editors.length > 0 && <div className="inline">{editors.map((editor) => {
          const document = docs.get(editor.document);
          const source = issue.evidence.find((anchor) => anchor.document === editor.document && anchor.scope !== "document")?.source;
          return <button className="button secondary" type="button" key={`${editor.document}:${editor.target}:${editor.field}`}
            onClick={() => editSource({ ...editor, source }, issue.id)}>
            {editors.length === 1 ? "Link source" : `Link source for ${document ? docName(document) : "quotation"}`}
          </button>;
        })}</div>}
        <ReviewForm key={`${issue.id}:${issue.fingerprint}:${issue.review.status}:${issue.review.note}`}
          batch={batch.id} issue={issue} hasNext={Boolean(next)} update={(value, advance) => {
            update(value);
            setSaved(true);
            if (advance && next) {
              setActive(next.id);
              requestAnimationFrame(() => document.getElementById(`issue-${next.id}`)?.focus());
            }
          }} />
        {saved && <span role="status" className="fine-print">Review saved.</span>}
      </div>
      <div className="evidence-heading"><h3>Source evidence</h3>
        {hasHighlights && <button className="button secondary" type="button" onClick={() => setFocus((value) => value + 1)}>
          <LocateFixed size={15} /> Focus highlights
        </button>}</div>
      {pages.size ? <div className="evidence-grid">
        {[...pages.entries()].map(([key, entry]) => {
          const doc = docs.get(entry.document);
          const editor = editors.find((entry) => entry.document === doc?.id);
          const missing = issue.rule === "missing" && entry.source === undefined;
          return doc ? <SourceViewer key={`${issue.id}:${key}`} doc={doc} initial={entry.source} anchors={entry.anchors}
            focus={focus} missing={missing} evidenceLabel={issue.detail?.source_labels[doc.id]}
            searchHint={issue.search_hint || (editor
              ? (termFields[editor.field] ?? lineFields[editor.field])?.split(" / ")[0] : undefined)}
            repair={missing ? (source) => editSource({ document: doc.id, target: "new", field: "material", source }, issue.id)
              : editor ? (source) => editSource({ ...editor, source }, issue.id) : undefined}
            /> : null;
        })}
      </div> : <Empty icon={<ClipboardList size={21} />} title="Source location unavailable">
        <p>{editors.length ? "Link the field to the source when you can verify it." : "Open the original documents to review this finding."}</p>
      </Empty>}
    </section> : <Empty icon={<ClipboardList size={22} />}
      title={reviewFilter === "pending" && filter === "all" ? "Review queue clear" : "No findings match these filters"}>
      {saved && <p role="status">Review saved.</p>}
    </Empty>}

  </div>;
}

function ReviewForm({ batch, issue, update, hasNext }: {
  batch: string; issue: Issue; hasNext: boolean; update: (batch: Batch, advance: boolean) => void;
}) {
  const [status, setStatus] = useState(issue.review.status);
  const [note, setNote] = useState(issue.review.note);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [advance, setAdvance] = useState(false);
  const noteCount = useId();
  return <form className="review-form" onSubmit={async (event) => {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const next = submitter instanceof HTMLButtonElement && submitter.value === "next";
    setAdvance(next); setBusy(true); setError("");
    try {
      update(await api<Batch>(`/batches/${batch}/issues/${issue.id}`, "PATCH", { status, note, fingerprint: issue.fingerprint }), next);
    } catch (error) {
      setError((error as Error).message);
    } finally { setBusy(false); }
  }}>
    <label>Review status<select aria-label="Review status" value={status} onChange={(event) => setStatus(event.target.value as ReviewStatus)}>
      {Object.entries(reviews).map(([key, label]) => <option key={key} value={key}
        disabled={key === "confirmed" && !issue.confirmable}>{label}</option>)}
    </select></label>
    <label className="grow review-note">Internal note
      <textarea aria-label="Internal note" aria-describedby={noteCount} placeholder="Optional review note"
        rows={3} value={note} maxLength={noteLimit} onChange={(event) => setNote(event.target.value)} />
      <small id={noteCount}>{note.length} / {noteLimit} characters</small>
    </label>
    <PendingButton className="button secondary" type="submit" disabled={busy} pending={busy && !advance} pendingLabel="Saving review…"
      icon={<Check size={15} />}>Save review</PendingButton>
    {error && <p className="error" role="alert">{error}</p>}
    {hasNext && <PendingButton className="button primary" type="submit" value="next" disabled={busy}
      pending={busy && advance} pendingLabel="Saving review…">Save and next</PendingButton>}
  </form>;
}
