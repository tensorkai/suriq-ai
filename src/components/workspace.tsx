"use client";

import {
  Activity as ActivityIcon, ArrowDownToLine, ChevronRight, Ellipsis,
  FileSearch, Files, Folder, Layers3, Plus, ScanLine, Table2, UploadCloud,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Activity, SupplierComparison } from "./comparison";
import { canChangeType, Documents } from "./documents";
import type { DocumentDestination } from "./documents";
import { Findings } from "./findings";
import { api } from "./model";
import type { Batch, Document, Project } from "./model";
import { LoadingNotice, Modal, PendingButton, Spinner } from "./shared";

const views = [
  { id: "documents", label: "Documents", Icon: Files },
  { id: "findings", label: "Findings", Icon: FileSearch },
  { id: "comparison", label: "Comparison", Icon: Table2 },
  { id: "activity", label: "Activity", Icon: ActivityIcon },
] as const;
type View = typeof views[number]["id"];

const blockerLabels = {
  upload: "Add missing documents", type: "Confirm document type", scope: "Set purchase scope",
  retry: "Open failed file", manage: "Manage source file", read: "Review document",
};
type Dialog = { kind: "project" } | { kind: "batch"; project: Project } |
  { kind: "upload"; revises?: Document } | { kind: "manageProject"; project: Project } | { kind: "manageBatch" } |
  { kind: "manageFile"; doc: Document };

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selection, setSelection] = useState("");
  const [projectSelection, setProjectSelection] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const [view, setView] = useState<View>("documents");
  const [docTarget, setDocTarget] = useState<{ destination?: DocumentDestination; key: number }>({ key: 0 });
  const [finding, setFinding] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const revision = useRef(0);
  const active = batch?.id === selection ? batch : null;
  const processingCount = active?.documents.filter((doc) => doc.status === "processing").length ?? 0;
  const processing = processingCount > 0;
  const project = projects.find((item) => item.id === projectSelection) ?? projects[0];

  function accept(value: Batch) {
    revision.current++;
    setBatch(value);
  }

  async function loadProjects() {
    const list = await api<Project[]>("/projects");
    setProjects(list);
    return list;
  }

  useEffect(() => {
    let cancelled = false;
    void api<Project[]>("/projects").then((items) => {
      if (cancelled) return;
      setProjects(items);
      const saved = window.localStorage.getItem("suriq-batch");
      const found = items.find((project) => project.batches.some((item) => item.id === saved));
      const first = found ?? items[0];
      setProjectSelection(first?.id ?? "");
      setSelection((found ? saved : first?.batches[0]?.id) ?? "");
    }).catch((error: Error) => { if (!cancelled) setError(error.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selection) return;
    window.localStorage.setItem("suriq-batch", selection);
    let cancelled = false;
    const current = revision.current;
    void api<Batch>(`/batches/${selection}`).then((data) => {
      if (!cancelled && revision.current === current) setBatch(data);
    }).catch((error: Error) => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [selection]);

  useEffect(() => {
    if (!selection || !processing) return;
    let cancelled = false;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (pending) return;
      const current = revision.current;
      pending = true;
      try {
        const data = await api<Batch>(`/batches/${selection}`);
        if (!cancelled && revision.current === current) setBatch(data);
      } catch (error) { if (!cancelled) setError((error as Error).message); }
      finally { pending = false; }
    }, 1250);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [selection, processing]);

  function openBatch(id: string, projectId: string) {
    setError(""); setProjectSelection(projectId); setSelection(id); setView("documents");
    setDocTarget({ key: 0 }); setFinding("");
    if (!id) window.localStorage.removeItem("suriq-batch");
  }

  async function create(kind: "project" | "batch", name: string, projectId?: string) {
    if (kind === "project") {
      const item = await api<{ id: string }>("/projects", "POST", { name });
      await loadProjects(); openBatch("", item.id);
    } else {
      const item = await api<{ id: string }>(`/projects/${projectId}/batches`, "POST", { name });
      await loadProjects(); openBatch(item.id, projectId!);
    }
  }

  function openDocument(destination: DocumentDestination) {
    setDocTarget((current) => ({ destination, key: current.key + 1 }));
    setView("documents");
  }

  function resolveBlocker() {
    const action = active?.verification_action;
    if (!active || !action) return;
    if (action.kind === "upload") {
      setDialog({ kind: "upload" });
    } else if (action.kind === "manage") {
      const doc = active.documents.find((item) => item.id === action.document);
      if (doc) setDialog({ kind: "manageFile", doc });
    } else {
      openDocument({ document: action.document, kind: action.kind });
    }
  }

  const blocked = active?.issues.filter((issue) => issue.severity === "Blocking").length ?? 0;
  const warnings = active?.issues.filter((issue) => issue.severity === "Warning").length ?? 0;
  const quotes = active?.documents.filter((doc) => doc.role === "quote" && !doc.superseded_by).length ?? 0;
  const canVerify = Boolean(active && !active.verification_problem);
  const verified = Boolean(active?.verified_at && !active.stale);

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon"><ScanLine size={24} strokeWidth={1.9} /></span>
        <span>SuriQ</span></div>
    </header>
    <div className="shell-body">
      <aside className="project-sidebar" aria-label="Projects and batches">
        <div className="sidebar-head"><span className="sidebar-label">Projects</span>
          <button className="icon-button" type="button" aria-label="New project" title="New project"
            onClick={() => setDialog({ kind: "project" })}><Plus size={18} /></button></div>
        {!projects.length && <div className="sidebar-empty">Your procurement projects will appear here.</div>}
        {projects.map((item) => <div className="project-group" key={item.id}>
          <div className={`project-line ${item.id === project?.id ? "current" : ""}`}>
            <button type="button" onClick={() => openBatch(item.batches[0]?.id ?? "", item.id)}>
              <Folder size={16} /><span>{item.name}</span></button>
            <button className="icon-button" type="button" aria-label={`Manage project ${item.name}`}
              onClick={() => setDialog({ kind: "manageProject", project: item })}><Ellipsis size={17} /></button>
          </div>
          {item.id === project?.id && <div className="batch-list">
            {item.batches.map((row) => <button key={row.id} type="button" aria-current={row.id === selection ? "page" : undefined}
              onClick={() => openBatch(row.id, item.id)}><Layers3 size={15} /><span>{row.name}</span></button>)}
            <button type="button" className="new-batch" onClick={() => setDialog({ kind: "batch", project: item })}>
              <Plus size={15} /> New batch</button>
          </div>}
        </div>)}
      </aside>
      <main className="main-content">
        {error && <div className="banner error" role="alert">{error}<button type="button" onClick={() => setError("")}>Dismiss</button></div>}
        {loading || (selection && !active && !error) ? <div className="panel"><LoadingNotice className="workspace-loading">{loading ? "Loading workspace…" : "Opening batch…"}</LoadingNotice></div>
          : !active ? <Welcome project={project} start={() => setDialog(project ? { kind: "batch", project } : { kind: "project" })} />
          : <>
            <header className="batch-header">
              <div className="breadcrumbs"><Folder size={14} />{active.project_name}<ChevronRight size={13} /><span>Procurement batch</span></div>
              <div className="batch-heading">
                <h1>{active.name}</h1>
                <div className="batch-actions">
                  <button className="icon-button batch-menu" type="button" aria-label="Manage batch"
                    onClick={() => setDialog({ kind: "manageBatch" })}><Ellipsis size={20} /></button>
                  <button className="button secondary" type="button" onClick={() => setDialog({ kind: "upload" })}
                    disabled={working}><UploadCloud size={16} /> Upload</button>
                  <PendingButton className="button primary" type="button" disabled={!canVerify} pending={working}
                    pendingLabel="Verifying…" icon={<ScanLine size={17} />} onClick={async () => {
                    setWorking(true); setError("");
                    try { accept(await api<Batch>(`/batches/${active.id}/verify`, "POST")); setFinding(""); setView("findings"); }
                    catch (error) { setError((error as Error).message); }
                    finally { setWorking(false); }
                  }}>Run verification</PendingButton>
                </div>
              </div>
              {processing ? <LoadingNotice className="batch-progress">
                Reading {processingCount} {processingCount === 1 ? "document" : "documents"}…
              </LoadingNotice> : active.documents.length > 0 && active.verification_problem &&
                <div className="notice inline">
                  <span role="status">{active.verification_problem}</span>
                  {active.verification_action && <button className="button secondary" type="button" onClick={resolveBlocker}>
                    {blockerLabels[active.verification_action.kind]}
                  </button>}
                </div>}
              <div className="batch-stats">
                <div><span>Source files</span><strong>{active.documents.length}</strong><small>{quotes} supplier {quotes === 1 ? "offer" : "offers"}</small></div>
                <div><span>Findings</span><strong>{verified ? active.issues.length : "—"}</strong><small>{verified ? `${blocked} blocking · ${warnings} warnings` : "Awaiting verification"}</small></div>
                <div><span>Purchase scope</span><strong className="stat-label">{active.scope_mode === "unknown" ? "Unset" : active.scope_mode === "all" ? "All BOM items" : `${active.scope.length} selected`}</strong>
                  <small>{active.scope_mode === "unknown" ? "Set from the BOM preview" : "Applied to coverage checks"}</small></div>
                <div><span>Verification</span><strong className="stat-label">{working ? "Checking" : processing ? "Waiting for files" : verified ? "Verified" : active.stale ? "Out of date" : "Not run"}</strong>
                  <small>{verified && active.verified_at ? new Date(active.verified_at).toLocaleString("en-PH") : active.stale ? "Run verification again" : "Checks against purchase scope"}</small></div>
              </div>
            </header>
            <section className="panel workspace-panel">
              <div className="viewbar">
                <nav aria-label="Batch workspace">{views.map(({ id, label, Icon }) => <button key={id} type="button"
                  aria-current={view === id ? "page" : undefined} onClick={() => {
                    if (id !== view && id === "documents") setDocTarget({ key: 0 });
                    if (id !== view && id === "findings") setFinding("");
                    setView(id);
                  }}>
                  <Icon size={16} />{label}{id === "findings" && verified && <span>{active.issues.length}</span>}</button>)}</nav>
                {verified && <details className="export-menu"><summary className="button secondary"><ArrowDownToLine size={16} /> Export</summary>
                  <div><a href={`/api/batches/${active.id}/export/pdf`}>Download PDF</a><a href={`/api/batches/${active.id}/export/csv`}>Download CSV</a></div></details>}
              </div>
              {view === "documents" && <Documents key={`${active.id}:${docTarget.key}`} batch={active} update={accept} destination={docTarget.destination}
                onEditorClose={docTarget.destination?.kind === "edit" ? () => setView("findings") : undefined}
                upload={() => setDialog({ kind: "upload" })} revise={(doc) => setDialog({ kind: "upload", revises: doc })} manage={(doc) => setDialog({ kind: "manageFile", doc })} />}
              {view === "findings" && <Findings key={`${active.id}:${finding}`} batch={active} update={accept} initial={finding}
                editSource={(editor, issueId) => {
                  setFinding(issueId);
                  openDocument({ document: editor.document, kind: "edit", editor });
                }} />}
              {view === "comparison" && <SupplierComparison batch={active} openFinding={(id) => { setFinding(id); setView("findings"); }} />}
              {view === "activity" && <Activity batch={active} />}
            </section>
          </>}
      </main>
    </div>
    {dialog?.kind === "project" && <NameDialog title="New project" label="Project name" submit="Create project"
      close={() => setDialog(null)} save={(name) => create("project", name)} />}
    {dialog?.kind === "batch" && <NameDialog title="New procurement batch" label="Batch name" submit="Create batch"
      close={() => setDialog(null)} save={(name) => create("batch", name, dialog.project.id)} />}
    {dialog?.kind === "upload" && active && <UploadDialog batch={active} revises={dialog.revises} close={() => setDialog(null)} update={accept} />}
    {dialog?.kind === "manageProject" && <NameDialog title="Manage project" label="Project name" submit="Save name"
      initial={dialog.project.name} close={() => setDialog(null)} save={async (name) => {
        await api(`/projects/${dialog.project.id}`, "PATCH", { name });
        await loadProjects();
        if (active?.project === dialog.project.id) accept(await api<Batch>(`/batches/${active.id}`));
      }} remove={async () => {
        await api(`/projects/${dialog.project.id}`, "DELETE");
        const list = await loadProjects();
        if (project?.id === dialog.project.id) openBatch(list[0]?.batches[0]?.id ?? "", list[0]?.id ?? "");
      }} deletion="Delete project and all its batches and source files" />}
    {dialog?.kind === "manageBatch" && active && <NameDialog title="Manage batch" label="Batch name" submit="Save name"
      initial={active.name} close={() => setDialog(null)} save={async (name) => {
        accept(await api<Batch>(`/batches/${active.id}`, "PATCH", { name })); await loadProjects();
      }} remove={async () => {
        await api(`/batches/${active.id}`, "DELETE");
        const list = await loadProjects();
        const current = list.find((project) => project.id === active.project);
        openBatch(current?.batches[0]?.id ?? "", active.project);
      }} deletion="Delete batch and its source files" />}
    {dialog?.kind === "manageFile" && <NameDialog title="Manage source file" initial={dialog.doc.name}
      close={() => setDialog(null)} remove={async () => {
        accept(await api<Batch>(`/documents/${dialog.doc.id}`, "DELETE"));
      }} deletion="Remove source from this batch"
      extra={canChangeType(dialog.doc) && <button className="button secondary" type="button" onClick={() => {
        openDocument({ document: dialog.doc.id, kind: "type" });
        setDialog(null);
      }}>Change document type</button>} />}
  </div>;
}

function Welcome({ project, start }: { project?: Project; start: () => void }) {
  return <section className="welcome">
    <div className="welcome-top">
      <h1>{project ? project.name : "Review supplier quotations"}</h1>
      <p>{project ? "Create a batch for the materials you’re purchasing." :
        "Add a bill of materials and up to five quotations. Review each difference alongside the original documents."}</p>
      <button className="button primary" type="button" onClick={start}>
        <Plus size={18} /> {project ? "Create batch" : "Create project"}</button>
    </div>
  </section>;
}

function NameDialog({ title, label, initial = "", submit, close, save, remove, deletion, extra }: {
  title: string; label?: string; initial?: string; submit?: string; close: () => void;
  save?: (value: string) => Promise<void>; remove?: () => Promise<void>; deletion?: string; extra?: React.ReactNode;
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const [working, setWorking] = useState<"save" | "remove" | null>(null);
  const [confirm, setConfirm] = useState(false);
  async function act(kind: "save" | "remove", action: () => Promise<void>) {
    setWorking(kind); setError("");
    try { await action(); close(); }
    catch (error) { setError((error as Error).message); }
    finally { setWorking(null); }
  }
  return <Modal title={title} close={close} busy={working !== null}>
    <form className="modal-body stack" onSubmit={(event) => {
      event.preventDefault();
      if (save && value.trim()) void act("save", () => save(value.trim()));
    }}>
      {label ? <label>{label}<input value={value} maxLength={120} required onChange={(event) => setValue(event.target.value)} /></label>
        : <p>{initial}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {save && <PendingButton className="button primary" type="submit" disabled={working !== null || !value.trim()}
        pending={working === "save"} pendingLabel={submit?.startsWith("Create") ? "Creating…" : "Saving…"}>{submit}</PendingButton>}
      {extra}
      {remove && (confirm ? <div className="notice"><strong>{deletion}?</strong><div className="inline">
        <PendingButton className="button danger" type="button" disabled={working !== null}
          pending={working === "remove"} pendingLabel="Removing…" onClick={() => void act("remove", remove)}>Confirm removal</PendingButton>
        <button className="button secondary" type="button" disabled={working !== null} onClick={() => setConfirm(false)}>Cancel</button>
      </div></div> : <button className="button danger" type="button" onClick={() => setConfirm(true)}>{deletion}</button>)}
    </form>
  </Modal>;
}

function UploadDialog({ batch, revises, update, close }: {
  batch: Batch; revises?: Document; update: (data: Batch) => void; close: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const [uploaded, setUploaded] = useState(0);
  const hostedReading = batch.ai.provider === "gemini" && batch.ai.configured;
  return <Modal title={revises ? "Upload revised quotation" : "Add BOM and supplier quotation files"} close={close} busy={busy}>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setUploaded(0); setError("");
      const failures: string[] = [];
      const remaining: File[] = [];
      try {
        for (const file of files) {
          const data = new FormData();
          data.append("file", file);
          if (revises) data.append("revises", revises.id);
          try { await api(`/batches/${batch.id}/documents`, "POST", data); }
          catch (error) { remaining.push(file); failures.push(`${file.name}: ${(error as Error).message}`); }
          finally { setUploaded((count) => count + 1); }
        }
        update(await api<Batch>(`/batches/${batch.id}`));
        if (failures.length) { setFiles(remaining); setError(failures.join(" • ")); } else close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>{revises ? `Revision of ${revises.name}. The original stays available in this batch.` :
        "Add the bill of materials and supplier quotations for this purchase."}</p>
      <label className="file-field" onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
        event.preventDefault();
        if (!busy) setFiles(Array.from(event.dataTransfer.files).slice(0, revises ? 1 : undefined));
      }}><span><UploadCloud size={19} /> Choose or drop PDF, XLSX, or DOCX files</span>
        <input type="file" name="file" accept=".pdf,.xlsx,.docx" multiple={!revises} disabled={busy}
          onChange={(event) => setFiles(Array.from(event.currentTarget.files ?? []))} /></label>
      {files.length > 0 && <p className="muted">{files.map((file) => file.name).join(", ")}</p>}
      {hostedReading && <p className="fine-print">Google processes document excerpts and page images to read these files.</p>}
      {busy && <div className="upload-progress">
        <div className="inline" role="status"><Spinner />{uploaded < files.length
          ? `Uploading file ${uploaded + 1} of ${files.length}` : "Opening uploaded files…"}</div>
        <progress aria-label="Upload progress" value={uploaded} max={files.length} />
      </div>}
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="button primary" aria-busy={busy} disabled={busy || !files.length}>{busy ? "Uploading…" : "Upload and extract"}</button>
    </form>
  </Modal>;
}
