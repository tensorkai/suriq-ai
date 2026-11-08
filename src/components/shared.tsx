"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { ComponentProps, ReactNode } from "react";

export function Modal({ title, children, close, wide = false, busy = false }: {
  title: string; children: ReactNode; close: () => void; wide?: boolean; busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className={wide ? "modal wide" : "modal"} aria-labelledby="modal-title"
    onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
    <div className="modal-header">
      <h2 id="modal-title">{title}</h2>
      <button className="icon-button" type="button" onClick={close} disabled={busy} aria-label="Close dialog"><X size={20} /></button>
    </div>
    {children}
  </dialog>;
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return <div className="empty"><span className="empty-icon">{icon}</span><h3>{title}</h3>{children}</div>;
}

export function Pill({ value, tone = "" }: { value: string; tone?: string }) {
  return <span className={`pill ${tone || value.toLowerCase().replaceAll(" ", "-")}`}>{value}</span>;
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function LoadingNotice({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`loading-notice ${className}`} role="status"><Spinner />{children}</div>;
}

export function PendingButton({ pending, pendingLabel, icon, children, disabled, ...props }:
  ComponentProps<"button"> & { pending: boolean; pendingLabel: string; icon?: ReactNode }) {
  return <button {...props} aria-busy={pending} disabled={disabled || pending}>
    {pending ? <><Spinner />{pendingLabel}</> : <>{icon}{children}</>}
  </button>;
}
