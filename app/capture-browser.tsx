"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { captureUrl } from "./capture-links.mjs";
import { searchCaptures } from "./capture-search.mjs";
import { thumbnailForCapture } from "./capture-thumbnails.mjs";

export type BrowserCapture = Readonly<{
  id: string; file: string; title: string; object: string; date: string;
  frames: number; exposure: string; source: string; rotation: number; printPreviewUrl: string | null;
}>;

function Thumbnail({ capture }: { capture: BrowserCapture }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const thumbnail = thumbnailForCapture(capture.file, capture.source, capture.rotation);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof IntersectionObserver === "undefined") {
      const reveal = () => setVisible(true);
      reveal(); return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) { setVisible(true); observer.disconnect(); }
    }, { rootMargin: "100px" });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <div ref={ref} className="browser-thumbnail">
    {visible && thumbnail && <img src={thumbnail.url} width={thumbnail.width} height={thumbnail.height} loading="lazy" decoding="async" alt={`${capture.title}, ${capture.object}, selected photograph`} />}
  </div>;
}

export default function CaptureBrowser({ captures, ready, onOpen }: { captures: readonly BrowserCapture[]; ready: boolean; onOpen: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [printsOnly, setPrintsOnly] = useState(false);
  const results = useMemo<readonly BrowserCapture[]>(() => searchCaptures(captures, query, printsOnly), [captures, query, printsOnly]);
  const start = (button: HTMLButtonElement, prints: boolean) => {
    opener.current = button;
    onOpen();
    setQuery(""); setPrintsOnly(prints); setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    if (!dialog.current?.open) dialog.current?.showModal();
    search.current?.focus();
  }, [open]);
  useEffect(() => {
    const onHistory = () => { if (dialog.current?.open) dialog.current.close(); };
    window.addEventListener("popstate", onHistory);
    return () => window.removeEventListener("popstate", onHistory);
  }, []);
  const close = () => { setOpen(false); opener.current?.focus(); };
  return <>
    <nav className="collection-entry" aria-label="Collection browser">
      <button type="button" disabled={!ready} onClick={event => start(event.currentTarget, false)}>Browse captures</button>
      <button type="button" disabled={!ready} onClick={event => start(event.currentTarget, true)}>Browse prints</button>
    </nav>
    <dialog ref={dialog} className="capture-browser" aria-labelledby="capture-browser-title" onClose={close} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); dialog.current?.close(); }
    }}>
      {open && <>
        <header className="browser-header"><div><p className="eyebrow">Northern Michigan · Observatory archive</p><h2 id="capture-browser-title">{printsOnly ? "Browse prints" : "Browse captures"}</h2></div><button type="button" onClick={() => dialog.current?.close()} aria-label="Close capture browser">Close</button></header>
        <div className="browser-controls">
          <label htmlFor="capture-search">Search captures</label>
          <div className="browser-search"><input ref={search} id="capture-search" type="search" placeholder="Object name or catalog number" value={query} onChange={event => setQuery(event.target.value)} aria-describedby="capture-browser-count" /><button type="button" disabled={!query} onClick={() => { setQuery(""); search.current?.focus(); }}>Clear search</button></div>
          <label className="browser-print-filter"><input type="checkbox" checked={printsOnly} onChange={event => setPrintsOnly(event.target.checked)} />Available prints only</label>
          <p id="capture-browser-count" role="status" aria-live="polite">{results.length} {results.length === 1 ? "capture" : "captures"}{printsOnly ? " with available prints" : ""}</p>
        </div>
        <div className="browser-results">
          {results.length === 0 ? <div className="browser-empty"><h3>No captures found</h3><p>Try a common name such as Pelican, or a catalog number such as M 31. Clear the search or turn off the print filter to see more observations.</p></div> : <ul className="browser-grid">{results.map(capture => <li key={capture.id} className="browser-card" data-capture-id={capture.id}>
            <Thumbnail capture={capture} />
            <div className="browser-card-copy"><h3>{capture.title}</h3><p className="browser-object">{capture.object}</p><p className="browser-context">{capture.date} · {capture.frames} × {capture.exposure}</p><div className="browser-card-links"><a href={captureUrl(capture.id)}>Open capture<span className="visually-hidden">: {capture.title}, {capture.object}, {capture.date}</span></a>{capture.printPreviewUrl && <a href={capture.printPreviewUrl}>View print<span className="visually-hidden">: {capture.title}, {capture.object}, {capture.date}</span></a>}</div></div>
          </li>)}</ul>}
        </div>
      </>}
    </dialog>
  </>;
}
