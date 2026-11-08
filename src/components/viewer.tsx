"use client";

import { ChevronLeft, ChevronRight, Download, FileWarning, LocateFixed, Maximize, Minus, Plus } from "lucide-react";
import { useLayoutEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { api, docName, documentFeedback, hasPreciseSource, sourceName } from "./model";
import type { Anchor, Box, Document } from "./model";
import { LoadingNotice, PendingButton } from "./shared";

export type Link = { source: number | string; cell?: string; box?: Box; text?: string };

function columnIndex(column: string) {
  return [...column].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0);
}

function columnName(index: number): string {
  return index <= 26 ? String.fromCharCode(64 + index) :
    columnName(Math.floor((index - 1) / 26)) + columnName((index - 1) % 26 + 1);
}

const FIT = 100;
const ZOOM_STEP = 25;
const MIN_ZOOM = 75;
const MAX_PDF_ZOOM = 400;
const MAX_TEXT_ZOOM = 200;

export function SourceViewer({ doc, initial, anchors = [], onLink, draft, focus = 0, missing, searchHint, repair }: {
  doc: Document; initial?: number | string; anchors?: Anchor[];
  onLink?: (link: Link) => void; draft?: Link; focus?: number;
  missing?: boolean; searchHint?: string; repair?: (source: number | string) => void;
}) {
  const pdf = doc.kind === "pdf";
  const activeFocus = anchors.some(hasPreciseSource) ? focus : 0;
  const [selection, setSelection] = useState({ source: initial ?? doc.sources[0]?.ref, focus: activeFocus });
  const selected = selection.focus === activeFocus ? selection.source : initial ?? selection.source;
  const [scale, setScale] = useState({ value: FIT, focus: activeFocus });
  const zoom = scale.focus === activeFocus ? scale.value : Math.max(scale.value, pdf ? 200 : 150);
  const maxZoom = pdf ? MAX_PDF_ZOOM : MAX_TEXT_ZOOM;
  const reference = anchors.find((anchor) => !hasPreciseSource(anchor));
  const overview = reference?.scope === "document";
  const [opened, setOpened] = useState(!overview);
  const [searchOpen, setSearchOpen] = useState(false);
  const [imageState, setImageState] = useState<Record<string, "ready" | "failed">>({});
  const [panMode, setPanMode] = useState(false);
  const start = useRef<[number, number] | null>(null);
  const drag = useRef<{ pointer: number; x: number; y: number; left: number; top: number } | null>(null);
  const pane = useRef<HTMLDivElement>(null);
  const paper = useRef<HTMLDivElement>(null);
  const zoomCenter = useRef<[number, number] | null>(null);
  const lastFocus = useRef(activeFocus);
  const selectId = useId();

  function select(source: number | string) {
    zoomCenter.current = null;
    lastFocus.current = activeFocus;
    pane.current?.scrollTo(0, 0);
    setSelection({ source, focus: activeFocus });
  }

  const index = Math.max(0, doc.sources.findIndex((source) => source.ref === selected));
  const source = doc.sources[index];
  const previewState = source && imageState[source.ref];
  const previewReady = previewState === "ready";
  const marking = previewReady && Boolean(onLink) && !panMode;
  const panning = previewReady && !marking;

  useLayoutEffect(() => {
    const container = pane.current;
    if (!container) return;
    const bounds = container.getBoundingClientRect();
    if (lastFocus.current !== activeFocus) {
      const marker = container.querySelector(".evidence-box, .marked-cell");
      if (!marker) return;
      lastFocus.current = activeFocus;
      zoomCenter.current = null;
      const target = marker.getBoundingClientRect();
      container.scrollTo(
        container.scrollLeft + target.left - bounds.left - (container.clientWidth - target.width) / 2,
        container.scrollTop + target.top - bounds.top - (container.clientHeight - target.height) / 2,
      );
    } else if (zoomCenter.current && paper.current) {
      const [x, y] = zoomCenter.current;
      const target = paper.current.getBoundingClientRect();
      container.scrollTo(
        container.scrollLeft + target.left - bounds.left + x * target.width - container.clientWidth / 2,
        container.scrollTop + target.top - bounds.top + y * target.height - container.clientHeight / 2,
      );
      zoomCenter.current = null;
    }
  }, [activeFocus, previewReady, source?.ref, zoom]);

  function changeZoom(value: number, fit = false) {
    const next = Math.max(MIN_ZOOM, Math.min(value, maxZoom));
    const container = pane.current;
    lastFocus.current = activeFocus;
    if (fit) {
      zoomCenter.current = null;
      container?.scrollTo(0, 0);
    } else if (next !== zoom && container && paper.current && previewReady) {
      const bounds = container.getBoundingClientRect();
      const target = paper.current.getBoundingClientRect();
      zoomCenter.current = [
        Math.min(1, Math.max(0, (bounds.left + container.clientWidth / 2 - target.left) / target.width)),
        Math.min(1, Math.max(0, (bounds.top + container.clientHeight / 2 - target.top) / target.height)),
      ];
    }
    setScale({ value: next, focus: activeFocus });
  }

  function handleZoomKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || previewState === "failed") return;
    if (event.key === "+" || event.key === "=") changeZoom(zoom + ZOOM_STEP);
    else if (event.key === "-") changeZoom(zoom - ZOOM_STEP);
    else if (event.key === "0") changeZoom(FIT, true);
    else return;
    event.preventDefault();
  }

  if (!source) {
    return <div className="empty small"><FileWarning size={22} /> Source preview is unavailable.</div>;
  }
  const scoped = anchors.filter((anchor) => anchor.source === source.ref && hasPreciseSource(anchor));
  const sheet = source.rows ?? [];
  const marked = new Set(scoped.flatMap((anchor) => anchor.cells ?? []));
  const maxColumn = Math.max(0, ...sheet.flatMap((row) => Object.keys(row.cells).map(columnIndex)));
  const columns = Array.from({ length: maxColumn }, (_, col) => columnName(col + 1));
  const path = `/api/documents/${doc.id}`;

  function position(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ] as [number, number];
  }

  return <article className="source-card">
    <header className="source-header">
      <span className={`role-tag ${doc.role}`}>{doc.role === "bom" ? "BOM" : doc.role === "quote" ? "QUOTE" : "REVIEW"}</span>
      <div className="source-title"><strong>{docName(doc)}</strong><small title={doc.name}>{doc.name}</small></div>
      <a className="icon-button" href={`${path}/original`} aria-label={`Download ${doc.name}`}><Download size={15} /></a>
    </header>
    {reference && <div className="source-reference">
      {!overview && doc.kind === "pdf" && typeof reference.source === "number" &&
        // eslint-disable-next-line @next/next/no-img-element -- thumbnail is generated from the original page
        <img className="source-thumbnail" src={`${path}/thumbnail/${reference.source}`} loading="lazy"
          alt={`Thumbnail of ${sourceName(reference.source)}`} />}
      <div>
        <strong>{missing ? "No matching line found" : overview ? "Document reference" : `Review ${sourceName(reference.source).toLowerCase()}`}</strong>
        {!overview && reference.text !== source.warning && reference.text && <p>{documentFeedback(reference.text)}</p>}
        <div className="inline source-actions">
          {overview && !opened && <button className="button secondary" type="button" onClick={() => setOpened(true)}>
            {doc.role === "quote" ? "Open quotation" : "Open document"}
          </button>}
          <button className="button secondary" type="button" aria-expanded={searchOpen}
            onClick={() => setSearchOpen(!searchOpen)}>{missing ? "Search quotation" : "Find in document"}</button>
        </div>
      </div>
    </div>}
    {!reference && searchHint && <div className="source-search">
      <button className="button secondary" type="button" aria-expanded={searchOpen}
        onClick={() => setSearchOpen(!searchOpen)}>Find in document</button>
    </div>}
    {searchOpen && <SourceSearch doc={doc.id} initial={searchHint} jump={(ref) => { select(ref); setOpened(true); }} />}
    {opened && <>
    <div className="source-navigation">
      <button className="icon-button" type="button" aria-label="Previous source" disabled={index === 0}
        onClick={() => select(doc.sources[index - 1].ref)}><ChevronLeft size={16} /></button>
      <label className="sr-only" htmlFor={selectId}>Source location</label>
      <select id={selectId} aria-label={`Source location in ${docName(doc)}`}
        value={index} onChange={(event) => select(doc.sources[Number(event.target.value)].ref)}>
        {doc.sources.map((item, idx) => <option value={idx} key={item.ref}>{sourceName(item.ref)}</option>)}
      </select>
      <button className="icon-button" type="button" aria-label="Next source" disabled={index === doc.sources.length - 1}
        onClick={() => select(doc.sources[index + 1].ref)}><ChevronRight size={16} /></button>
      <span className="source-method">{doc.kind === "docx" ? "Document excerpt" : source.method === "cells" ? "Worksheet" : source.method === "ocr" ? "Scanned page" : "PDF page"}</span>
    </div>
    {repair && (reference || searchOpen) && <div className="source-search">
      <button className="button secondary" type="button" onClick={() => repair(source.ref)}>
        {missing ? "Link a visible line" : "Link source on this page"}
      </button>
    </div>}
    {doc.kind === "docx" && <p className="source-warning">Browse the extracted sections or open the paginated original.
      {" "}<a href={`${path}/rendered`} target="_blank" rel="noreferrer">Open paginated preview</a>
    </p>}
    {source.warning && <p className="source-warning"><FileWarning size={15} /> {documentFeedback(source.warning)}</p>}
    {previewState !== "failed" && <div className="preview-tools">
      {pdf && onLink && <button className="button secondary pan-toggle" type="button" aria-pressed={panMode}
        onClick={() => setPanMode(!panMode)}>Pan preview</button>}
      {pdf && !onLink && zoom > FIT && <span className="pan-hint">Drag to pan</span>}
      <Zoom value={zoom} max={maxZoom} pdf={pdf} change={changeZoom} />
    </div>}
    {pdf ? <div className="paper-scroll" ref={pane} role="region" tabIndex={0} aria-label={`PDF preview of ${doc.name}`}
      onKeyDown={handleZoomKey}>
      {previewState === "failed" ? <p className="empty small">Could not open this page. Download the original to view it.</p> :
        <div ref={paper} className={marking ? "pdf-page marking" : panning && zoom > FIT ? "pdf-page pannable" : "pdf-page"}
          style={{ width: `${zoom}%` }}
          onPointerDown={previewReady ? (event) => {
            if (event.button !== 0 || !event.isPrimary) return;
            if (marking) {
              start.current = position(event);
            } else {
              const container = pane.current;
              if (!container || event.pointerType !== "mouse" ||
                (container.scrollWidth <= container.clientWidth && container.scrollHeight <= container.clientHeight)) return;
              drag.current = {
                pointer: event.pointerId, x: event.clientX, y: event.clientY,
                left: container.scrollLeft, top: container.scrollTop,
              };
              container.focus({ preventScroll: true });
              event.preventDefault();
            }
            event.currentTarget.setPointerCapture(event.pointerId);
          } : undefined}
          onPointerMove={panning ? (event) => {
            const from = drag.current;
            if (from?.pointer === event.pointerId) {
              pane.current?.scrollTo(from.left + from.x - event.clientX, from.top + from.y - event.clientY);
            }
          } : undefined}
          onPointerUp={(event) => {
            if (!event.isPrimary) return;
            if (drag.current?.pointer === event.pointerId) drag.current = null;
            if (!onLink || !start.current) return;
            const [x, y] = start.current;
            const [endX, endY] = position(event);
            start.current = null;
            const box: Box = [Math.min(x, endX), Math.min(y, endY), Math.abs(x - endX), Math.abs(y - endY)];
            if (box[2] > .003 && box[3] > .003) onLink({ source: source.ref, box });
          }}
          onLostPointerCapture={() => { start.current = null; drag.current = null; }}>
          {!previewReady && <LoadingNotice className="preview-loading">Loading {sourceName(source.ref).toLowerCase()}…</LoadingNotice>}
          {/* eslint-disable-next-line @next/next/no-img-element -- raster source coordinates are intrinsic */}
          <img key={source.ref} className={previewReady ? "" : "preview-pending"} src={`${path}/page/${source.ref}`} alt={`Full source ${sourceName(source.ref)} of ${doc.name}`}
            style={source.width && source.height ? { aspectRatio: `${source.width} / ${source.height}` } : undefined}
            draggable={false} loading="lazy"
            onLoad={() => setImageState((state) => ({ ...state, [source.ref]: "ready" as const }))}
            onError={() => setImageState((state) => ({ ...state, [source.ref]: "failed" as const }))} />
          {previewReady && scoped.flatMap((anchor, i) => (anchor.boxes ?? []).map(([x, y, w, h], j) =>
            <span key={`${i}-${j}`} className="evidence-box" title={anchor.text} style={{
              left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%`,
            }} />))}
          {previewReady && draft?.source === source.ref && draft.box && <span className="evidence-box draft" style={{
            left: `${draft.box[0] * 100}%`, top: `${draft.box[1] * 100}%`,
            width: `${draft.box[2] * 100}%`, height: `${draft.box[3] * 100}%`,
          }} />}
        </div>}
    </div> : <div className="sheet-scroll" ref={pane} style={{ fontSize: `${zoom}%` }}>
      {columns.length ? <table className="sheet"><thead><tr><th scope="col">#</th>
        {columns.map((col) => <th scope="col" key={col}>{col}</th>)}</tr></thead>
        <tbody>{sheet.map(({ row, cells }) => <tr key={row}><th scope="row">{row}</th>
          {columns.map((col) => {
            const cell = `${col}${row}`;
            return <td key={col} data-cell={cell} title={cell}
              className={marked.has(cell) || (draft?.source === source.ref && draft.cell === cell) ? "marked-cell" : ""}>
              {onLink && cells[col] ? <button type="button" title={`Link ${cell}`}
                onClick={() => onLink({ source: source.ref, cell })}>{cells[col]}</button> : cells[col]}
            </td>;
          })}</tr>)}</tbody></table> : <p className="empty small">This worksheet has no populated cells.</p>}
    </div>}
    {scoped.length > 0 && <ul className="evidence-text" aria-label="Highlighted evidence">
      {scoped.map((anchor, idx) => <li key={idx}><LocateFixed size={14} />
        <span>{anchor.cells?.length ? `${anchor.cells.join(", ")} · ` : ""}{anchor.text}
          {anchor.weak ? " · Needs review" : ""}
        </span>
      </li>)}
    </ul>}
    </>}
  </article>;
}

type SearchResult = {
  searchable: boolean; partial: boolean; limited: boolean; matches: { source: number | string; text: string }[];
};

function SourceSearch({ doc, initial, jump }: { doc: string; initial?: string; jump: (source: number | string) => void }) {
  const [query, setQuery] = useState(initial?.slice(0, 120) ?? "");
  const [result, setResult] = useState<SearchResult & { query: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <div className="source-search">
    <form className="inline" onSubmit={async (event) => {
      event.preventDefault();
      setBusy(true); setError(""); setResult(undefined);
      try {
        const response = await api<SearchResult>(`/documents/${doc}/search`, "POST", { query });
        setResult({ ...response, query: query.trim().replace(/\s+/g, " ") });
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <label className="grow">Find in document<input type="search" value={query} maxLength={120}
        autoFocus disabled={busy} onChange={(event) => setQuery(event.target.value)} /></label>
      <PendingButton className="button secondary" type="submit" disabled={!query.trim()} pending={busy}
        pendingLabel="Searching…">Search</PendingButton>
    </form>
    {error && <p role="alert" className="error">{error}</p>}
    {result && <div aria-live="polite">
      <h4>Search matches</h4>
      {!result.searchable ? <p>This document has no searchable text. Open it to inspect the pages.</p> :
        result.matches.length ? <ul>{result.matches.map((match, index) => {
          const start = match.text.toLowerCase().indexOf(result.query.toLowerCase());
          const end = start + result.query.length;
          return <li key={index}><button type="button" className="source-excerpt" onClick={() => jump(match.source)}>
            <strong>{sourceName(match.source)}</strong> {start < 0 ? match.text : <>
              {match.text.slice(0, start)}<mark>{match.text.slice(start, end)}</mark>{match.text.slice(end)}
            </>}
          </button></li>;
        })}</ul> : <p>No matches in the searchable text.</p>}
      {result.searchable && result.partial && <p>Search text is incomplete on some pages or sections.</p>}
      {result.limited && <p>Showing the first 50 matches. Narrow your search to see more specific results.</p>}
    </div>}
  </div>;
}

function Zoom({ value, max, pdf, change }: {
  value: number; max: number; pdf: boolean; change: (value: number, fit?: boolean) => void;
}) {
  const reset = pdf ? "Fit to width" : "Reset text size";
  return <div className="zoom" role="group" aria-label="Preview zoom">
    <button className="icon-button" type="button" aria-label="Zoom out" title="Zoom out (−)" disabled={value <= MIN_ZOOM}
      onClick={() => change(value - ZOOM_STEP)}><Minus size={15} /></button>
    <output className="zoom-level">{value}%</output>
    <button className="icon-button" type="button" aria-label="Zoom in" title="Zoom in (+)" disabled={value >= max}
      onClick={() => change(value + ZOOM_STEP)}><Plus size={15} /></button>
    <button className="icon-button zoom-fit" type="button" aria-label={reset} title={pdf ? "Fit to width (0)" : reset}
      onClick={() => change(FIT, true)}><Maximize size={15} /></button>
  </div>;
}
