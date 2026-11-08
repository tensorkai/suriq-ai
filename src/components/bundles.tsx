"use client";

import { useRef, useState } from "react";
import { api } from "./model";
import type { Anchor, Batch, BundleLayout, Document, ReferenceLink } from "./model";
import { Modal, PendingButton } from "./shared";
import { SourceViewer } from "./viewer";

export function BundleDetails({ doc, layout, quotes, references, update, open }: {
  doc: Document; layout: BundleLayout; quotes: Document[]; references: ReferenceLink[];
  update: (batch: Batch) => void; open: (id: string, anchor?: Anchor) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [page, setPage] = useState(layout.pages[0]);
  const [anchor, setAnchor] = useState<Anchor>();
  function show(number: number, evidence?: Anchor) {
    setPage(number);
    setAnchor(evidence);
  }
  return <>
    <div className="notice stack">
      <strong>{layout.offers.length ? "Quotation bundle" : "Quotation reference"}</strong>
      <p>Page numbers refer to the original PDF. Reference pages stay outside supplier pricing.</p>
      {layout.offers.map((offer) => {
        const quote = quotes.find((item) => item.bundle_pages?.join() === offer.pages.join());
        const label = [offer.supplier, offer.reference, `Pages ${offer.pages.join(", ")}`].filter(Boolean).join(" · ");
        return quote ? <button type="button" className="button secondary" key={offer.pages[0]}
          onClick={() => open(quote.id)}>{label}</button> : <p key={offer.pages[0]}>{label}</p>;
      })}
      {layout.reference_pages.length > 0 && <div className="inline"><span>Reference pages</span>
        {layout.reference_pages.map((number) => <button className="button secondary" type="button"
          key={number} onClick={() => show(number)}>Page {number}</button>)}</div>}
      {layout.pending_pages.length > 0 && <div className="inline"><span>Pages awaiting assignment</span>
        {layout.pending_pages.map((number) => <button className="button secondary" type="button"
          key={number} onClick={() => show(number)}>Page {number}</button>)}</div>}
      {layout.excluded.map((entry) => <p key={entry.page}>Page {entry.page} excluded · {entry.reason}</p>)}
      <button className="button secondary" type="button" disabled={doc.status === "processing"}
        onClick={() => setEditing(true)}>Edit page assignments</button>
    </div>
    {references.length > 0 && <details className="notice">
      <summary>Linked reference amounts</summary>
      {references.map((entry, index) => <div className="stack" key={index}>
        <strong>{entry.supplier} · {entry.reference} · {entry.basis}</strong>
        <p>{entry.scope} · {entry.matches ? "Matches original" : "Reference discrepancy"}</p>
        <div className="inline">
          <button className="button secondary" type="button" onClick={() => show(entry.source, entry.evidence[0])}>
            Reference page {entry.source} · {entry.reference_value}
          </button>
          <button className="button secondary" type="button" onClick={() => open(entry.quote, entry.evidence[1])}>
            Original quotation · {entry.original_value}
          </button>
        </div>
      </div>)}
    </details>}
    <div className="source-single">
      <SourceViewer key={`${doc.id}:${page}`} doc={doc} initial={page} anchors={anchor ? [anchor] : []} />
    </div>
    {editing && <BundleEditor key={layout.revision} doc={doc} layout={layout} close={() => setEditing(false)}
      update={update} />}
  </>;
}

function pageRange(text: string, allowed: number[]) {
  const result = [];
  for (const part of text.split(",").filter((entry) => entry.trim())) {
    const bounds = part.trim().match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
    if (!bounds) throw new Error("Use page numbers and ranges such as 1–3, 5.");
    const first = Number(bounds[1]);
    const last = Number(bounds[2] ?? bounds[1]);
    if (!allowed.includes(first) || !allowed.includes(last) || first > last) {
      throw new Error("Use page numbers from this original PDF.");
    }
    for (let page = first; page <= last; page++) result.push(page);
  }
  return result;
}

function BundleEditor({ doc, layout, update, close }: {
  doc: Document; layout: BundleLayout; update: (batch: Batch) => void; close: () => void;
}) {
  const [offers, setOffers] = useState(() => layout.offers.map((offer, id) => ({ ...offer, id, pages: offer.pages.join(", ") })));
  const [reference, setReference] = useState(layout.reference_pages.join(", "));
  const [excluded, setExcluded] = useState(() => layout.excluded.map((entry, id) => ({ ...entry, id, pages: String(entry.page) })));
  const nextId = useRef(layout.offers.length + layout.excluded.length);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save(undo: boolean) {
    setBusy(true);
    setError("");
    try {
      const assignments = undo ? { undo: true } : {
        offers: offers.map(({ supplier, reference, pages }) => ({ supplier, reference, pages: pageRange(pages, layout.pages) })),
        reference_pages: pageRange(reference, layout.pages),
        excluded: excluded.flatMap(({ reason, pages }) => pageRange(pages, layout.pages).map((page) => ({ page, reason }))),
      };
      update(await api<Batch>(`/documents/${doc.id}/bundle`, "PATCH",
        { ...assignments, revision: layout.revision, confirm_archive: confirm }));
      close();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <Modal title="Edit quotation bundle" close={close} busy={busy}>
    <form className="modal-body stack" onSubmit={(event) => { event.preventDefault(); void save(false); }}>
      <p>Assign each original page once. Use ranges such as 1–3, 5. Add an offer to split a range or combine ranges under one offer to merge it.</p>
      {layout.pending_pages.length > 0 && <p className="notice">Unassigned pages · {layout.pending_pages.join(", ")}</p>}
      {offers.map((offer, index) => <fieldset className="notice stack" key={offer.id}>
        <legend>Offer {index + 1}</legend>
        <label>Supplier<input required maxLength={120} value={offer.supplier} onChange={(event) =>
          setOffers((items) => items.map((item) => item.id === offer.id ? { ...item, supplier: event.target.value } : item))} /></label>
        <label>Quotation reference<input maxLength={120} value={offer.reference} onChange={(event) =>
          setOffers((items) => items.map((item) => item.id === offer.id ? { ...item, reference: event.target.value } : item))} /></label>
        <label>Offer pages<input required value={offer.pages} onChange={(event) =>
          setOffers((items) => items.map((item) => item.id === offer.id ? { ...item, pages: event.target.value } : item))} /></label>
        <button type="button" className="button secondary"
          onClick={() => setOffers((items) => items.filter((item) => item.id !== offer.id))}>Remove offer assignment</button>
      </fieldset>)}
      <button className="button secondary" type="button" disabled={offers.length >= 5}
        onClick={() => setOffers([...offers, { id: nextId.current++, supplier: "", reference: "", pages: "" }])}>Add offer assignment</button>
      <label>Reference pages<input value={reference} onChange={(event) => setReference(event.target.value)} /></label>
      {excluded.map((entry, index) => <fieldset className="notice stack" key={entry.id}>
        <legend>Exclusion {index + 1}</legend>
        <label>Excluded pages<input required value={entry.pages} onChange={(event) =>
          setExcluded((items) => items.map((item) => item.id === entry.id ? { ...item, pages: event.target.value } : item))} /></label>
        <label>Reason<input required maxLength={500} value={entry.reason} onChange={(event) =>
          setExcluded((items) => items.map((item) => item.id === entry.id ? { ...item, reason: event.target.value } : item))} /></label>
        <button type="button" className="button secondary"
          onClick={() => setExcluded((items) => items.filter((item) => item.id !== entry.id))}>Remove exclusion</button>
      </fieldset>)}
      <button type="button" className="button secondary"
        onClick={() => setExcluded([...excluded, { id: nextId.current++, page: 0, reason: "", pages: "" }])}>Exclude pages</button>
      {(confirm || error.startsWith("Page assignments moved")) && <label className="inline">
        <input type="checkbox" checked={confirm} onChange={(event) => setConfirm(event.target.checked)} />
        Keep existing source corrections with the earlier split
      </label>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="inline">
        <PendingButton className="button primary" pending={busy} pendingLabel="Saving assignments…" type="submit">Save page assignments</PendingButton>
        {layout.undo_revision != null && <button className="button secondary" disabled={busy} type="button"
          onClick={() => void save(true)}>Undo last split change</button>}
      </div>
    </form>
  </Modal>;
}
