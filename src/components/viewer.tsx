"use client";

import { ChevronLeft, ChevronRight, Download, FileWarning, LocateFixed, Minus, Plus } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { PointerEvent } from "react";
import { docName, sourceName } from "./model";
import type { Anchor, Box, Document } from "./model";

export type Link = { source: number | string; cell?: string; box?: Box; text?: string };

function columnIndex(column: string) {
  return [...column].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0);
}

function columnName(index: number): string {
  return index <= 26 ? String.fromCharCode(64 + index) :
    columnName(Math.floor((index - 1) / 26)) + columnName((index - 1) % 26 + 1);
}

export function SourceViewer({ doc, initial, anchors = [], zoom = 100, onLink, draft, focus = 0 }: {
  doc: Document; initial?: number | string; anchors?: Anchor[]; zoom?: number;
  onLink?: (link: Link) => void; draft?: Link; focus?: number;
}) {
  const [selection, setSelection] = useState({ source: initial ?? doc.sources[0]?.ref, focus });
  const selected = selection.focus === focus ? selection.source : initial;
  const select = (source: number | string) => setSelection({ source, focus });
  const [imageError, setImageError] = useState<number | string>();
  const start = useRef<[number, number] | null>(null);
  const pane = useRef<HTMLDivElement>(null);
  const lastFocus = useRef(focus);
  const selectId = useId();
  useEffect(() => {
    if (lastFocus.current === focus) return;
    lastFocus.current = focus;
    const timer = requestAnimationFrame(() => {
      const container = pane.current;
      const marker = container?.querySelector(".evidence-box, .marked-cell");
      if (!container || !marker) return;
      const bounds = container.getBoundingClientRect();
      const target = marker.getBoundingClientRect();
      container.scrollTo({
        left: container.scrollLeft + target.left - bounds.left - (bounds.width - target.width) / 2,
        top: container.scrollTop + target.top - bounds.top - (bounds.height - target.height) / 2,
      });
    });
    return () => cancelAnimationFrame(timer);
  }, [focus, initial]);
  const index = Math.max(0, doc.sources.findIndex((source) => source.ref === selected));
  const source = doc.sources[index];
  if (!source) {
    return <div className="empty small"><FileWarning size={22} /> Source preview is unavailable.</div>;
  }
  const scoped = anchors.filter((anchor) => anchor.source === source.ref);
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
      <span className={`role-tag ${doc.role}`}>{doc.role === "bom" ? "BOM" : "QUOTE"}</span>
      <div className="source-title"><strong>{docName(doc)}</strong><small title={doc.name}>{doc.name}</small></div>
      <a className="icon-button" href={`${path}/original`} aria-label={`Download ${doc.name}`}><Download size={15} /></a>
    </header>
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
      <span className="source-method">{source.method === "cells" ? "WORKSHEET" : source.method === "ocr" ? "OCR" : "PDF TEXT"}</span>
    </div>
    {source.warning && <p className="source-warning"><FileWarning size={15} /> {source.warning}</p>}
    {doc.kind === "pdf" ? <div className="paper-scroll" ref={pane}>
      {imageError === source.ref ? <p className="empty small">Page rendering failed. Download the original to inspect it.</p> :
        <div className={`pdf-page ${onLink ? "marking" : ""}`} style={{ width: `${zoom}%` }}
          onPointerDown={onLink ? (event) => {
            if (event.button !== 0) return;
            start.current = position(event);
            event.currentTarget.setPointerCapture(event.pointerId);
          } : undefined}
          onPointerUp={onLink ? (event) => {
            if (!start.current) return;
            const [x, y] = start.current;
            const [endX, endY] = position(event);
            start.current = null;
            const box: Box = [Math.min(x, endX), Math.min(y, endY), Math.abs(x - endX), Math.abs(y - endY)];
            if (box[2] > .003 && box[3] > .003) onLink({ source: source.ref, box });
          } : undefined}
          onPointerCancel={() => { start.current = null; }}>
          {/* eslint-disable-next-line @next/next/no-img-element -- raster source coordinates are intrinsic */}
          <img src={`${path}/page/${source.ref}`} alt={`Full source ${sourceName(source.ref)} of ${doc.name}`}
            style={source.width && source.height ? { aspectRatio: `${source.width} / ${source.height}` } : undefined}
            draggable={false} loading="lazy" onError={() => setImageError(source.ref)} />
          {scoped.flatMap((anchor, i) => (anchor.boxes ?? []).map(([x, y, w, h], j) =>
            <span key={`${i}-${j}`} className="evidence-box" title={anchor.text} style={{
              left: `${x * 100}%`, top: `${y * 100}%`, width: `${w * 100}%`, height: `${h * 100}%`,
            }} />))}
          {draft?.source === source.ref && draft.box && <span className="evidence-box draft" style={{
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
        <span>{anchor.cells?.length ? `${anchor.cells.join(", ")} · ` : ""}{anchor.text || "Source needs review"}</span>
      </li>)}
    </ul>}
  </article>;
}

export function Zoom({ value, change }: { value: number; change: (value: number) => void }) {
  return <div className="zoom" aria-label="Document zoom">
    <button className="icon-button" type="button" aria-label="Zoom out" disabled={value <= 75}
      onClick={() => change(value - 25)}><Minus size={15} /></button>
    <button className="zoom-reset" type="button" title="Reset zoom" onClick={() => change(100)}>{value}%</button>
    <button className="icon-button" type="button" aria-label="Zoom in" disabled={value >= 200}
      onClick={() => change(value + 25)}><Plus size={15} /></button>
  </div>;
}
