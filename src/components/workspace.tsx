"use client";

import {
  Activity as ActivityIcon, ArrowDownToLine, ArrowRight, ChevronRight, CircleDot, Ellipsis,
  FileSearch, Files, Folder, FolderPlus, Layers3, Plus, ScanLine, ShieldCheck, Table2, UploadCloud,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Activity, SupplierComparison } from "./comparison";
import { Documents } from "./documents";
import { Findings } from "./findings";
import { api } from "./model";
import type { Batch, Document, Project } from "./model";
import { Empty, Modal } from "./shared";

const views = [
  { id: "documents", label: "Documents", Icon: Files },
  { id: "findings", label: "Findings", Icon: FileSearch },
  { id: "comparison", label: "Comparison", Icon: Table2 },
  { id: "activity", label: "Activity", Icon: ActivityIcon },
] as const;
type View = typeof views[number]["id"];
type Dialog = { kind: "project" } | { kind: "batch"; project: Project } |
  { kind: "upload" } | { kind: "manageProject"; project: Project } | { kind: "manageBatch" } |
  { kind: "manageFile"; doc: Document };

export function Workspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selection, setSelection] = useState("");
  const [projectSelection, setProjectSelection] = useState("");
  const [batch, setBatch] = useState<Batch | null>(null);
  const [view, setView] = useState<View>("documents");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const revision = useRef(0);
  const active = batch?.id === selection ? batch : null;
  const processing = active?.documents.some((doc) => doc.status === "processing") ?? false;
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

  const blocked = active?.issues.filter((issue) => issue.severity === "Blocking").length ?? 0;
  const warnings = active?.issues.filter((issue) => issue.severity === "Warning").length ?? 0;
  const quotes = active?.documents.filter((doc) => doc.role === "quote").length ?? 0;
  const canVerify = active?.documents.some((doc) => doc.role === "bom") && quotes > 0 &&
    active.documents.every((doc) => doc.status === "ready" || doc.status === "needs_review");
  const verified = Boolean(active?.verified_at && !active.stale);

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-icon"><ScanLine size={24} strokeWidth={1.9} /></span>
        <span>SuriQ<span className="brand-ai"> AI</span><small>PROCUREMENT VERIFICATION</small></span></div>
      <div className="local-badge"><span /><ShieldCheck size={15} /> Local workspace</div>
    </header>
    <div className="shell-body">
      <aside className="project-sidebar" aria-label="Projects and batches">
        <div className="sidebar-head"><span className="eyebrow">WORKSPACE</span>
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
        <div className="sidebar-foot"><ShieldCheck size={17} /><span><strong>On-device processing</strong><small>Local files and verification</small></span></div>
      </aside>
      <main className="main-content">
        {error && <div className="banner error" role="alert">{error}<button type="button" onClick={() => setError("")}>Dismiss</button></div>}
        {loading || (selection && !active && !error) ? <div className="panel"><Empty icon={<CircleDot size={24} />} title="Loading workspace" /></div>
          : !active ? <Welcome project={project} start={() => setDialog(project ? { kind: "batch", project } : { kind: "project" })} />
          : <>
            <header className="batch-header">
              <div className="breadcrumbs"><Folder size={14} />{active.project_name}<ChevronRight size={13} /><span>Procurement batch</span></div>
              <div className="batch-heading">
                <div><h1>{active.name}</h1><p>Verify supplier offers against your purchase requirements.</p></div>
                <div className="batch-actions">
                  <button className="icon-button batch-menu" type="button" aria-label="Manage batch"
                    onClick={() => setDialog({ kind: "manageBatch" })}><Ellipsis size={20} /></button>
                  <button className="button secondary" type="button" onClick={() => setDialog({ kind: "upload" })}
                    disabled={active.documents.length >= 6}><UploadCloud size={16} /> Upload</button>
                  <button className="button primary" type="button" disabled={!canVerify || working} onClick={async () => {
                    setWorking(true); setError("");
                    try { accept(await api<Batch>(`/batches/${active.id}/verify`, "POST")); setView("findings"); }
                    catch (error) { setError((error as Error).message); }
                    finally { setWorking(false); }
                  }}><ScanLine size={17} /> {working ? "Verifying…" : "Run verification"}</button>
                </div>
              </div>
              <div className="batch-stats">
                <div><span>Source files</span><strong>{active.documents.length}</strong><small>{quotes} supplier {quotes === 1 ? "offer" : "offers"}</small></div>
                <div><span>Findings</span><strong>{verified ? active.issues.length : "—"}</strong><small>{verified ? `${blocked} blocking · ${warnings} warnings` : "Awaiting verification"}</small></div>
                <div><span>Purchase scope</span><strong className="stat-label">{active.scope_mode === "unknown" ? "Unset" : active.scope_mode === "all" ? "All BOM items" : `${active.scope.length} selected`}</strong>
                  <small>{active.scope_mode === "unknown" ? "Set from the BOM preview" : "Applied to coverage checks"}</small></div>
                <div><span>Verification</span><strong className="stat-label">{processing ? "Extracting" : verified ? "Verified" : active.stale ? "Out of date" : "Not run"}</strong>
                  <small>{verified && active.verified_at ? new Date(active.verified_at).toLocaleString("en-PH") : "Local deterministic checks"}</small></div>
              </div>
            </header>
            <section className="panel workspace-panel">
              <div className="viewbar">
                <nav aria-label="Batch workspace">{views.map(({ id, label, Icon }) => <button key={id} type="button"
                  aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
                  <Icon size={16} />{label}{id === "findings" && verified && <span>{active.issues.length}</span>}</button>)}</nav>
                {verified && <details className="export-menu"><summary className="button secondary"><ArrowDownToLine size={16} /> Export</summary>
                  <div><a href={`/api/batches/${active.id}/export/pdf`}>Download PDF</a><a href={`/api/batches/${active.id}/export/csv`}>Download CSV</a></div></details>}
              </div>
              {view === "documents" && <Documents key={active.id} batch={active} update={accept}
                upload={() => setDialog({ kind: "upload" })} manage={(doc) => setDialog({ kind: "manageFile", doc })} />}
              {view === "findings" && <Findings key={active.id} batch={active} update={accept} />}
              {view === "comparison" && <SupplierComparison batch={active} />}
              {view === "activity" && <Activity batch={active} />}
            </section>
          </>}
      </main>
    </div>
    {dialog?.kind === "project" && <NameDialog title="New project" label="Project name" submit="Create project"
      close={() => setDialog(null)} save={(name) => create("project", name)} />}
    {dialog?.kind === "batch" && <NameDialog title="New procurement batch" label="Batch name" submit="Create batch"
      close={() => setDialog(null)} save={(name) => create("batch", name, dialog.project.id)} />}
    {dialog?.kind === "upload" && active && <UploadDialog batch={active} close={() => setDialog(null)} update={accept} />}
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
      }} deletion="Remove source from this batch" />}
  </div>;
}

function Welcome({ project, start }: { project?: Project; start: () => void }) {
  const steps = [
    { Icon: FolderPlus, label: "Organize a purchase", text: "Keep procurement rounds and supplier files together by project." },
    { Icon: Files, label: "Add your sources", text: "Upload one bill of materials and up to five PDF or Excel quotations." },
    { Icon: FileSearch, label: "Review the evidence", text: "Inspect source highlights, resolve findings, and compare offers." },
  ];
  return <section className="welcome">
    <div className="welcome-top">
      <span className="eyebrow"><ScanLine size={17} /> PROCUREMENT WORKSPACE</span>
      <h1>{project ? project.name : "Procurement verification"}</h1>
      <p>{project ? "Create a procurement batch for your next purchase round." :
        "Check supplier quotations against material requirements and trace every finding back to the original document."}</p>
      <button className="button primary" type="button" onClick={start}>
        <Plus size={18} /> {project ? "Create batch" : "Create project"} <ArrowRight size={16} /></button>
    </div>
    <div className="workflow-grid">{steps.map(({ Icon, label, text }, index) => <article key={label}>
      <div><Icon size={22} /><span>0{index + 1}</span></div><h2>{label}</h2><p>{text}</p>
    </article>)}</div>
  </section>;
}

function NameDialog({ title, label, initial = "", submit, close, save, remove, deletion }: {
  title: string; label?: string; initial?: string; submit?: string; close: () => void;
  save?: (value: string) => Promise<void>; remove?: () => Promise<void>; deletion?: string;
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [confirm, setConfirm] = useState(false);
  async function act(action: () => Promise<void>) {
    setWorking(true); setError("");
    try { await action(); close(); }
    catch (error) { setError((error as Error).message); }
    finally { setWorking(false); }
  }
  return <Modal title={title} close={close}>
    <form className="modal-body stack" onSubmit={(event) => {
      event.preventDefault();
      if (save && value.trim()) void act(() => save(value.trim()));
    }}>
      {label ? <label>{label}<input value={value} maxLength={120} required onChange={(event) => setValue(event.target.value)} /></label>
        : <p>{initial}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {save && <button className="button primary" type="submit" disabled={working || !value.trim()}>{submit}</button>}
      {remove && (confirm ? <div className="notice"><strong>{deletion}?</strong><div className="inline">
        <button className="button danger" type="button" disabled={working} onClick={() => void act(remove)}>Confirm removal</button>
        <button className="button secondary" type="button" onClick={() => setConfirm(false)}>Cancel</button>
      </div></div> : <button className="button danger" type="button" onClick={() => setConfirm(true)}>{deletion}</button>)}
    </form>
  </Modal>;
}

function UploadDialog({ batch, update, close }: { batch: Batch; update: (data: Batch) => void; close: () => void }) {
  const hasBom = batch.documents.some((doc) => doc.role === "bom");
  const quotes = batch.documents.filter((doc) => doc.role === "quote").length;
  const [role, setRole] = useState(hasBom ? "quote" : "bom");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <Modal title="Upload source document" close={close}>
    <form className="modal-body stack" onSubmit={async (event) => {
      event.preventDefault(); setBusy(true); setError("");
      const data = new FormData(event.currentTarget);
      try {
        await api(`/batches/${batch.id}/documents`, "POST", data);
        update(await api<Batch>(`/batches/${batch.id}`)); close();
      } catch (error) { setError((error as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>PDF and XLSX originals stay in the local workspace.</p>
      <label>Document role<select name="role" value={role} onChange={(event) => setRole(event.target.value)}>
        <option value="bom" disabled={hasBom}>Bill of materials</option>
        <option value="quote" disabled={quotes >= 5}>Supplier quotation</option>
      </select></label>
      {role === "quote" && <>
        <label>Supplier name<input name="supplier" placeholder="e.g. North Steel Supply" maxLength={120} required /></label>
        <label>Supplier coverage<select name="coverage" defaultValue="unknown">
          <option value="unknown">Coverage unknown</option><option value="full">Full scope</option><option value="partial">Partial offer</option>
        </select></label>
      </>}
      <label className="file-field"><span><UploadCloud size={19} /> Choose a PDF or XLSX</span><input type="file" name="file" accept=".pdf,.xlsx" required /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="button primary" disabled={busy || (hasBom && quotes >= 5)}>{busy ? "Uploading…" : "Upload and extract"}</button>
    </form>
  </Modal>;
}
