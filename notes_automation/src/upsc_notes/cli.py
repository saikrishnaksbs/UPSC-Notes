"""Command-line interface: `upsc-notes <command>` (or `python -m upsc_notes`)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import load_config


def _fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(str(c).ljust(w) for c, w in zip(r, widths)) for r in rows]
    return "\n".join(out)


def cmd_serve(args, cfg) -> None:
    import uvicorn

    from .web import create_app

    host, port = args.host or cfg.host, args.port or cfg.port
    url = f"http://{host}:{port}"
    print(f"UPSC Notes Studio {__version__}\n  notes repo : {cfg.repo_root}\n  model      : {cfg.llm.model} via {cfg.llm.provider}\n  open       : {url}")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


def _progress_printer(store, job_id: str, stop: threading.Event) -> None:
    last = ""
    while not stop.is_set():
        try:
            j = store.load(job_id)
            msg = f"[{j.stage or j.status}] {j.message or ''}"
            if j.progress_total:
                msg += f" ({j.progress_done}/{j.progress_total})"
            if msg != last:
                print("  " + msg, flush=True)
                last = msg
        except Exception:
            pass
        stop.wait(2)


def cmd_ingest(args, cfg) -> None:
    from .jobs import Cancelled, JobStore
    from .models import JobOptions
    from .pipeline import Context, apply_job, run_job

    store, ctx = JobStore(cfg), Context(cfg)
    for f in args.files:
        path = Path(f)
        if not path.is_file():
            sys.exit(f"File not found: {f}")
        opts = JobOptions(model=args.model, pages=args.pages, skip_irrelevant=not args.keep_irrelevant, auto_approve_threshold=args.auto_approve)
        job = store.create(path, path.name, args.tag, args.source_url, opts)
        print(f"Job {job.id}: {path.name}  (tag: {job.tag})")
        if job.duplicate_of:
            print(f"  note: this file was ingested before ({job.duplicate_of})")
        stop = threading.Event()
        t = threading.Thread(target=_progress_printer, args=(store, job.id, stop), daemon=True)
        t.start()
        store.acquire_run_lock(job.id)
        store.update(job.id, lambda j: setattr(j, "status", "running"))
        started = time.time()
        try:
            run_job(cfg, store, ctx, job.id, threading.Event())
        except (Cancelled, KeyboardInterrupt):
            store.update(job.id, lambda j: setattr(j, "status", "cancelled"))
            stop.set()
            sys.exit("Cancelled — resume later with the web UI.")
        except Exception as exc:
            store.update(job.id, lambda j: (setattr(j, "status", "failed"), setattr(j, "error", str(exc))))
            stop.set()
            sys.exit(f"Failed: {exc}")
        stop.set()
        store.release_run_lock(job.id)
        print(f"Done in {(time.time() - started) / 60:.1f} min.")
        _print_proposals(store, job.id)
        if args.apply:
            res = apply_job(cfg, store, ctx, job.id, commit=args.commit, branch=args.branch)
            _print_apply(res)
        else:
            print(f"\nReview in the web UI (upsc-notes serve), or: upsc-notes apply {job.id}")


def _print_proposals(store, job_id: str, content: bool = False) -> None:
    props = store.load_proposals(job_id)
    topics = store.load_topics(job_id)
    skipped = [t for t in topics if t.relevant is False]
    rows = [
        [p.id, p.action, p.status, f"{p.confidence:.2f}", (p.target_path or f"{p.subject}/{p.subfolder}")[:70], p.title[:60], "⚠" if p.flags else ""]
        for p in props
    ]
    print(_fmt_table(rows, ["id", "action", "status", "conf", "destination", "title", ""]))
    if skipped:
        print(f"\nSkipped {len(skipped)} topic(s): " + "; ".join(f"{t.title[:40]} ({t.skip_reason})" for t in skipped[:10]))
    failed = [t for t in topics if t.stage == "error"]
    if failed:
        print(f"\nFailed {len(failed)} topic(s): " + "; ".join(f"{t.title[:40]} ({(t.error or '')[:120]})" for t in failed[:10]))
    if content:
        for p in props:
            print(f"\n{'=' * 80}\n{p.id} {p.action} → {p.target_path or p.subject + '/' + p.subfolder}\n{'=' * 80}\n{p.content or '(no content)'}")
            for f in p.flags:
                print(f"  ⚠ {f}")


def _print_apply(res: dict) -> None:
    print(f"Applied {len(res['applied'])} proposal(s); wrote {len(res['written'])} file(s).")
    for w in res["written"]:
        print(f"  {w}")
    for pid, err in res.get("errors", {}).items():
        print(f"  ! {pid}: {err}")
    for w in res.get("warnings", []):
        print(f"  ~ {w}")
    if res.get("commit"):
        print(f"Committed {res['commit']['commit'][:8]} on branch {res['commit']['branch']}")
    if res.get("git_error"):
        print(f"Git: {res['git_error']}")
    if res.get("pr"):
        print(f"Pull request: {res['pr'].get('pr_url')}")


def cmd_jobs(args, cfg) -> None:
    from .jobs import JobStore

    store = JobStore(cfg)
    rows = []
    for j in store.list():
        props = store.load_proposals(j.id)
        rows.append([j.id, j.status, j.tag[:36], j.filename[:40], str(len(props)), j.created_at[:16]])
    print(_fmt_table(rows, ["job", "status", "tag", "file", "proposals", "created"]) if rows else "No jobs yet.")


def cmd_show(args, cfg) -> None:
    from .jobs import JobStore

    store = JobStore(cfg)
    job = store.load(args.job)
    print(f"{job.tag} · {job.filename} · {job.status} · model {job.model}\n")
    _print_proposals(store, job.id, content=args.content)


def cmd_approve(args, cfg) -> None:
    from .jobs import JobStore

    store = JobStore(cfg)
    with store.lock(args.job):
        props = store.load_proposals(args.job)
        n = 0
        for p in props:
            chosen = p.id in args.ids if args.ids else (p.confidence >= args.min_confidence and (args.include_flagged or not p.flags))
            if chosen and p.status == "ready" and p.content:
                p.status = "approved"
                n += 1
        store.save_proposals(args.job, props)
    print(f"Approved {n} proposal(s).")


def cmd_apply(args, cfg) -> None:
    from .jobs import JobStore
    from .pipeline import Context, apply_job

    store = JobStore(cfg)
    res = apply_job(cfg, store, Context(cfg), args.job, proposal_ids=args.ids or None, commit=args.commit, branch=args.branch, push_pr=args.pr)
    _print_apply(res)


def cmd_models(args, cfg) -> None:
    from .llm import build_chat_model, list_ollama_models

    llm = build_chat_model(cfg)
    print(f"Configured: {cfg.llm.model} via {cfg.llm.provider} at {cfg.llm.base_url} → {llm.health()}")
    print(f"Embeddings: {cfg.embeddings.model} via {cfg.embeddings.provider}")
    models = list_ollama_models(cfg.llm.base_url) if cfg.llm.provider == "ollama" else []
    if models:
        print(_fmt_table([[m["name"], m["kind"], m["parameters"] or "", m["quantization"] or "", f"{m['size_gb']} GB"] for m in models], ["model", "kind", "params", "quant", "size"]))


def cmd_extract(args, cfg) -> None:
    from .ingest import extract_document
    from .segment import segment_document

    doc = extract_document(args.file, ocr=cfg.ocr, engine=args.engine or cfg.extraction.engine, pages=args.pages)
    for w in doc.warnings:
        print(f"! {w}", file=sys.stderr)
    if args.segments:
        segs = segment_document(doc, min_chars=cfg.pipeline.min_segment_chars, max_chars=cfg.pipeline.max_segment_chars)
        rows = [[str(s.id), s.pages, str(len(s.text)), (s.section or "").split(" › ")[-1][:30], s.title[:80]] for s in segs]
        print(_fmt_table(rows, ["#", "pages", "chars", "section", "title"]))
    else:
        print(doc.to_markdown())


def cmd_eval(args, cfg) -> None:
    from .evaluate import evaluate

    report = evaluate(cfg, model=args.model, sample=args.sample, seed=args.seed, mode=args.mode)
    rows = report.pop("rows")
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps({**report, "rows": rows}, indent=2))


def cmd_index(args, cfg) -> None:
    from .pipeline import Context

    t = time.time()
    tax, idx = Context(cfg).index(lambda d, n, m: print(f"  {m}", flush=True))
    print(f"Indexed {len(idx.notes)} notes in {len(tax.subjects)} subjects ({time.time() - t:.1f}s)" + (f"; embeddings unavailable: {idx.embedding_error}" if idx.embedding_error else ""))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="upsc-notes", description="Turn study material into organised UPSC notes with a local LLM.")
    ap.add_argument("--config", help="Path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="Start the web app")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("ingest", help="Process files from the command line (batch)")
    p.add_argument("files", nargs="+")
    p.add_argument("--tag", required=True, help="Source tag, e.g. 'Vision IAS September 2026'")
    p.add_argument("--source-url")
    p.add_argument("--pages", help="PDF page range, e.g. 8-40")
    p.add_argument("--model", help="Override the local model, e.g. qwen2.5:14b")
    p.add_argument("--auto-approve", type=float, help="Pre-approve proposals at or above this confidence")
    p.add_argument("--keep-irrelevant", action="store_true", help="Do not skip adverts/contents/quiz pages")
    p.add_argument("--apply", action="store_true", help="Apply approved proposals when done")
    p.add_argument("--commit", action="store_true", help="With --apply: commit the written files")
    p.add_argument("--branch", help="With --commit: branch name")

    sub.add_parser("jobs", help="List uploads")
    p = sub.add_parser("show", help="Show a job's proposals")
    p.add_argument("job")
    p.add_argument("--content", action="store_true")

    p = sub.add_parser("approve", help="Approve proposals")
    p.add_argument("job")
    p.add_argument("ids", nargs="*")
    p.add_argument("--min-confidence", type=float, default=0.75)
    p.add_argument("--include-flagged", action="store_true")

    p = sub.add_parser("apply", help="Write approved proposals into the repository")
    p.add_argument("job")
    p.add_argument("ids", nargs="*")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--branch")
    p.add_argument("--pr", action="store_true", help="Push the branch and open a pull request (gh)")

    sub.add_parser("models", help="Show local model status")
    sub.add_parser("index", help="Build/refresh the search index of existing notes")

    p = sub.add_parser("extract", help="Debug: show extracted Markdown or topic segments (no LLM)")
    p.add_argument("file")
    p.add_argument("--pages")
    p.add_argument("--segments", action="store_true")
    p.add_argument("--engine", choices=["docling", "pymupdf"])

    p = sub.add_parser("eval", help="Benchmark a model on placing your existing notes")
    p.add_argument("--model")
    p.add_argument("--sample", type=int, default=40)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--mode", choices=["new", "duplicate"], default="new")
    p.add_argument("--out", help="Write the full report (JSON) here")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    handler = globals()[f"cmd_{args.cmd}"]
    handler(args, cfg)


if __name__ == "__main__":
    main()
