"use strict";
/* UPSC Notes Studio — single-page UI (no build step). All dynamic text goes through textContent;
   only Markdown rendered by the server (raw HTML disabled) is inserted as HTML. */

const $ = (sel, root = document) => root.querySelector(sel);
// replaceChildren would render null/false as text; drop them (and flatten arrays).
const _rc = Element.prototype.replaceChildren;
Element.prototype.replaceChildren = function (...kids) {
  return _rc.apply(this, kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false));
};

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k === "value") el.value = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const api = {
  async req(method, url, body, form) {
    const opts = { method, headers: {} };
    if (form) opts.body = body;
    else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const detail = data && data.detail ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : res.statusText;
      throw new Error(detail);
    }
    return data;
  },
  get(u) { return this.req("GET", u); },
  post(u, b) { return this.req("POST", u, b === undefined ? {} : b); },
  patch(u, b) { return this.req("PATCH", u, b); },
  del(u) { return this.req("DELETE", u); },
  upload(u, form) { return this.req("POST", u, form, true); },
};

const S = {
  status: null, taxonomy: null, jobs: [], view: "home", jobId: null,
  job: null, sel: null, filter: "all", search: "", tab: "proposals", sub: "preview",
  preview: {}, drafts: {}, dest: {}, files: [], timer: null, lastSel: "", busyApply: false,
};

// ------------------------------------------------------------------ utilities
function toast(msg, bad) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (bad ? " bad" : "");
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = "toast"), bad ? 6000 : 3000);
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
function pct(x) { return `${Math.round((x || 0) * 100)}%`; }
function confColor(c) { return c >= 0.75 ? "var(--ok)" : c >= 0.55 ? "var(--warn)" : "var(--bad)"; }
function human(name) {
  return (name || "").replace(/^\d+[-_]/, "").replace(/[-_]+/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}
function badge(text, cls) { return h("span", { class: `badge ${cls || text}` }, text); }
function subjectsList() { return (S.taxonomy && S.taxonomy.subjects) || []; }
function folderTitle(subject, folder) {
  const s = subjectsList().find((x) => x.name === subject);
  const f = s && s.subfolders.find((x) => x.name === folder);
  return `${s ? s.title : subject} › ${f ? f.title : human(folder)}`;
}
function running(job) { return job && (job.status === "running" || job.status === "queued"); }

// ------------------------------------------------------------------ status bar
async function loadStatus() {
  try {
    S.status = await api.get("/api/status");
  } catch (e) {
    S.status = null;
  }
  const st = S.status;
  const box = $("#status");
  box.replaceChildren();
  if (!st) {
    box.append(h("span", { class: "chip" }, h("span", { class: "dot bad" }), "Server unreachable"));
    return;
  }
  const llmOk = st.llm.ok;
  box.append(
    h("span", { class: "chip", title: st.llm.base_url },
      h("span", { class: `dot ${llmOk ? "ok" : "bad"}` }),
      llmOk ? `${st.llm.model}` : (st.llm.online === false ? "Ollama offline" : `${st.llm.model} not installed`)),
    h("span", { class: "chip", title: "OCR for images and scanned PDFs" },
      h("span", { class: `dot ${st.ocr.tesseract ? "ok" : "warn"}` }), st.ocr.tesseract ? "OCR ready" : "No OCR"),
    st.repo.is_repo
      ? h("span", { class: "chip", title: st.repo.path }, h("span", { class: "dot ok" }), `git: ${st.repo.branch}`)
      : h("span", { class: "chip" }, h("span", { class: "dot warn" }), "not a git repo"),
  );
}

async function loadTaxonomy(force) {
  if (!S.taxonomy || force) S.taxonomy = await api.get("/api/taxonomy");
}

// ------------------------------------------------------------------ router
function route() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/job\/([\w-]+)/);
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.remove("active"));
  clearTimeout(S.timer);
  if (m) {
    if (S.jobId !== m[1]) { S.sel = null; S.preview = {}; S.tab = "proposals"; S.filter = "all"; S.search = ""; }
    S.view = "job"; S.jobId = m[1];
    loadJob(true);
  } else if (hash.startsWith("#/library")) {
    S.view = "library";
    $("[data-nav=library]").classList.add("active");
    renderLibrary();
  } else {
    S.view = "home";
    $("[data-nav=home]").classList.add("active");
    loadJobs(true);
  }
}
window.addEventListener("hashchange", route);

function schedule(fn, ms) {
  clearTimeout(S.timer);
  S.timer = setTimeout(fn, ms);
}

// ------------------------------------------------------------------ home
async function loadJobs(full) {
  try {
    S.jobs = await api.get("/api/jobs");
  } catch (e) {
    toast(e.message, true);
  }
  if (S.view !== "home") return;
  if (full || !$("#jobs")) renderHome();
  else renderJobsList();
  if (S.jobs.some(running)) schedule(() => loadJobs(false), 3000);
}

function renderHome() {
  const app = $("#app");
  const accepts = (S.status && S.status.accepts) || [".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg"];
  const input = h("input", { type: "file", multiple: true, accept: accepts.join(","), hidden: true, onchange: (e) => addFiles(e.target.files) });
  const drop = h("div", { class: "drop", onclick: () => input.click() },
    h("strong", {}, "Drop PDFs, images, Markdown or text files"),
    h("span", {}, "or click to browse — magazines, newspapers, clippings, notes"));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

  const tags = [...new Set(S.jobs.map((j) => j.tag))];
  const models = (S.status && S.status.models) || [];
  const defModel = S.status ? S.status.llm.model : "";
  const form = h("form", { id: "upload", onsubmit: submitUpload },
    drop, input,
    h("ul", { class: "files", id: "files" }),
    h("label", { class: "field" }, h("span", {}, "Source tag *"),
      h("input", { type: "text", name: "tag", required: true, list: "tags", placeholder: "e.g. Vision IAS September 2026, The Hindu, PIB" }),
      h("datalist", { id: "tags" }, tags.map((t) => h("option", { value: t }))),
      h("small", {}, "Shown on every generated note and used for traceability.")),
    h("label", { class: "field" }, h("span", {}, "Source link (optional)"),
      h("input", { type: "url", name: "source_url", placeholder: "https://…" })),
    h("details", { class: "advanced" }, h("summary", {}, "Options"),
      h("label", { class: "field" }, h("span", {}, "Pages (PDF only)"),
        h("input", { type: "text", name: "pages", placeholder: "all — or e.g. 8-40, 55" }),
        h("small", {}, "Process part of a long magazine first to check the results.")),
      h("label", { class: "field" }, h("span", {}, "Local model"),
        h("select", { name: "model" },
          h("option", { value: "" }, `Default (${defModel})`),
          models.map((m) => h("option", { value: m.name }, `${m.name}  ·  ${m.parameters || ""} ${m.quantization || ""}`)))),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "skip_irrelevant", checked: true }), "Skip ads, contents pages and quizzes"),
      h("label", { class: "field" }, h("span", {}, "Auto-approve at confidence ≥"),
        h("input", { type: "number", name: "auto_approve_threshold", min: "0", max: "1", step: "0.05", placeholder: "off (review everything)" }),
        h("small", {}, "Proposals with warnings are never auto-approved."))),
    h("button", { class: "btn primary block", type: "submit", id: "go" }, "Extract & organise"));

  app.replaceChildren(
    h("div", { class: "grid-home" },
      h("section", { class: "panel" }, h("header", {}, h("h2", {}, "New upload")), h("div", { class: "body" }, form)),
      h("section", { class: "panel" },
        h("header", {}, h("h2", {}, "Uploads"), h("span", { class: "spacer" }),
          h("button", { class: "btn ghost small", onclick: () => loadJobs(false) }, "Refresh")),
        h("ul", { class: "jobs", id: "jobs" }))));
  renderFiles();
  renderJobsList();
}

function addFiles(list) {
  for (const f of list) if (!S.files.some((x) => x.name === f.name && x.size === f.size)) S.files.push(f);
  renderFiles();
}
function renderFiles() {
  const ul = $("#files");
  if (!ul) return;
  ul.replaceChildren(...S.files.map((f, i) =>
    h("li", {}, h("span", {}, f.name, h("span", { class: "faint" }, `  ${(f.size / 1048576).toFixed(1)} MB`)),
      h("button", { class: "btn ghost small", type: "button", onclick: () => { S.files.splice(i, 1); renderFiles(); } }, "Remove"))));
}

async function submitUpload(e) {
  e.preventDefault();
  if (!S.files.length) return toast("Choose at least one file", true);
  const f = e.target;
  const fd = new FormData();
  S.files.forEach((file) => fd.append("files", file));
  fd.append("tag", f.tag.value.trim());
  fd.append("source_url", f.source_url.value.trim());
  fd.append("pages", f.pages.value.trim());
  fd.append("model", f.model.value);
  fd.append("skip_irrelevant", f.skip_irrelevant.checked ? "true" : "false");
  fd.append("auto_approve_threshold", f.auto_approve_threshold.value.trim());
  const btn = $("#go");
  btn.disabled = true; btn.textContent = "Uploading…";
  try {
    const jobs = await api.upload("/api/jobs", fd);
    S.files = [];
    toast(`Queued ${jobs.length} upload${jobs.length > 1 ? "s" : ""}`);
    location.hash = `#/job/${jobs[0].id}`;
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "Extract & organise";
  }
}

function renderJobsList() {
  const ul = $("#jobs");
  if (!ul) return;
  if (!S.jobs.length) {
    ul.replaceChildren(h("li", { class: "empty" }, "No uploads yet. Add a magazine, newspaper or notes file to begin."));
    return;
  }
  ul.replaceChildren(...S.jobs.map((j) => {
    const c = j.counts || {};
    const side = [badge(j.status)];
    if (running(j)) side.push(h("div", { class: "progress" }, h("i", { style: `width:${j.progress_total ? (100 * j.progress_done) / j.progress_total : 5}%` })));
    if (c.total) side.push(h("span", { class: "small muted" }, `${c.create || 0} new · ${c.update || 0} updates` + (c.applied ? ` · ${c.applied} applied` : "")));
    return h("li", {}, h("a", { class: "job", href: `#/job/${j.id}` },
      h("span", { class: "tag" }, j.tag),
      h("span", { class: "meta" }, `${j.filename} · ${ago(j.created_at)} · ${j.model || ""}` + (running(j) && j.message ? ` · ${j.message}` : "") + (j.status === "failed" && j.error ? ` · ${j.error}` : "")),
      h("span", { class: "side" }, side)));
  }));
}

// ------------------------------------------------------------------ job page
async function loadJob(full) {
  if (S.view !== "job") return;
  try {
    await loadTaxonomy();
    S.job = await api.get(`/api/jobs/${S.jobId}`);
  } catch (e) {
    $("#app").replaceChildren(h("div", { class: "empty" }, e.message));
    return;
  }
  if (S.view !== "job") return;
  const props = S.job.proposals;
  if (!S.sel || !props.some((p) => p.id === S.sel)) S.sel = props.length ? visibleProposals()[0]?.id || props[0].id : null;
  if (full || !$("#jobview")) renderJob();
  else refreshJob();
  const busy = Object.keys(S.job.busy || {}).length || props.some((p) => p.status === "pending" || p.status === "generating");
  if (running(S.job.job) || busy) schedule(() => loadJob(false), 2500);
}

function visibleProposals() {
  const q = S.search.toLowerCase();
  return S.job.proposals.filter((p) => {
    if (q && !(p.title.toLowerCase().includes(q) || `${p.subject} ${p.subfolder}`.toLowerCase().includes(q))) return false;
    switch (S.filter) {
      case "review": return p.status === "ready" && (p.flags.length || p.confidence < 0.6);
      case "create": return p.action === "create";
      case "update": return p.action === "update";
      case "approved": return p.status === "approved";
      case "rejected": return p.status === "rejected";
      case "applied": return p.status === "applied";
      default: return true;
    }
  });
}

function renderJob() {
  const app = $("#app");
  app.replaceChildren(h("div", { id: "jobview", class: "panel" },
    h("div", { id: "jobhead" }), h("div", { class: "tabs", id: "jobtabs" }), h("div", { id: "jobbody" })));
  refreshJob(true);
}

function refreshJob(force) {
  renderJobHead();
  renderTabs();
  if (S.tab === "proposals") {
    if (force || !$("#plist")) renderProposalsTab();
    else { renderToolbar(); renderList(); maybeRenderDetail(); }
  } else if (S.tab === "skipped") renderSkippedTab();
  else if (S.tab === "document") renderDocumentTab(force);
}

function renderJobHead() {
  const { job, counts, document: doc } = S.job;
  const head = $("#jobhead");
  const pct = job.progress_total ? Math.round((100 * job.progress_done) / job.progress_total) : 0;
  const approved = counts.approved || 0;
  const actions = [];
  if (running(job)) actions.push(h("button", { class: "btn small", onclick: () => jobAction("cancel") }, "Cancel"));
  if (["failed", "cancelled"].includes(job.status)) actions.push(h("button", { class: "btn small", onclick: () => jobAction("resume") }, "Resume"));
  if (!running(job)) actions.push(h("button", { class: "btn small ghost", onclick: deleteJob }, "Delete"));
  actions.push(h("button", { class: "btn primary", disabled: !approved || running(job), onclick: openApply }, `Apply approved (${approved})`));
  const topics = S.job.topics;
  const skipped = topics.filter((t) => t.relevant === false || t.stage === "error").length;
  head.replaceChildren(
    h("div", { class: "jobhead" },
      h("div", {},
        h("h1", {}, job.tag, " ", badge(job.status)),
        h("div", { class: "sub" }, `${job.filename} · ${job.model || ""} · ${ago(job.created_at)}` + (job.source_url ? " · " : ""),
          job.source_url ? h("a", { href: job.source_url, target: "_blank", rel: "noopener" }, "source link") : null)),
      h("div", { class: "row wrap" }, actions)),
    running(job)
      ? h("div", { class: "alert info" }, h("div", { class: "row" }, h("span", {}, job.message || job.stage || "Working…"), h("span", { class: "spacer" }), `${pct}%`),
          h("div", { class: "progress", style: "width:100%;margin-top:6px" }, h("i", { style: `width:${Math.max(pct, 3)}%` })))
      : null,
    job.status === "failed" ? h("div", { class: "alert bad" }, `Failed: ${job.error || "unknown error"}. Fix the cause and press Resume — finished work is kept.`) : null,
    job.duplicate_of ? h("div", { class: "alert" }, `This file was already ingested before (${job.duplicate_of}). Re-applying is safe: existing updates are detected and skipped.`) : null,
    h("div", { class: "stats" },
      stat(topics.length, "topics found"),
      stat(counts.create || 0, "new notes"),
      stat(counts.update || 0, "updates to existing notes"),
      stat(skipped, "skipped"),
      stat(counts.needs_review || 0, "need review"),
      stat(counts.applied || 0, "applied"),
      doc ? stat(doc.pages_processed, "pages read") : null,
      job.stats && job.stats.llm_seconds ? stat(`${Math.round(job.stats.llm_seconds / 60)} min`, "model time") : null));
}
function stat(v, label) { return h("div", { class: "stat" }, h("b", {}, String(v)), h("span", {}, label)); }

function renderTabs() {
  const topics = S.job.topics;
  const skipped = topics.filter((t) => t.relevant === false || t.stage === "error").length;
  const tabs = [["proposals", `Proposals (${S.job.proposals.length})`], ["skipped", `Skipped topics (${skipped})`], ["document", "Document"]];
  $("#jobtabs").replaceChildren(...tabs.map(([id, label]) =>
    h("button", { class: S.tab === id ? "active" : "", onclick: () => { S.tab = id; refreshJob(true); } }, label)));
}

async function jobAction(what) {
  try { await api.post(`/api/jobs/${S.jobId}/${what}`); toast(what === "cancel" ? "Cancelling…" : "Resumed"); loadJob(true); }
  catch (e) { toast(e.message, true); }
}
async function deleteJob() {
  if (!confirm("Delete this upload and its proposals? Notes already applied to the repository are not affected.")) return;
  try { await api.del(`/api/jobs/${S.jobId}`); location.hash = "#/"; }
  catch (e) { toast(e.message, true); }
}

// ---------- proposals tab
function renderProposalsTab() {
  $("#jobbody").replaceChildren(
    h("div", { class: "toolbar", id: "toolbar" }),
    h("div", { class: "split" }, h("div", { class: "plist", id: "plist" }), h("div", { class: "detail", id: "detail" })));
  renderToolbar();
  renderList();
  renderDetail();
}

function renderToolbar() {
  const c = S.job.counts;
  const filters = [["all", `All ${c.total}`], ["review", `Needs review ${c.needs_review || 0}`], ["create", `New ${c.create}`], ["update", `Updates ${c.update}`], ["approved", `Approved ${c.approved || 0}`], ["rejected", `Rejected ${c.rejected || 0}`], ["applied", `Applied ${c.applied || 0}`]];
  const search = h("input", { type: "search", placeholder: "Filter by title or folder…", value: S.search });
  search.addEventListener("input", (e) => { S.search = e.target.value; renderList(); });
  $("#toolbar").replaceChildren(
    h("div", { class: "filters" }, filters.map(([id, label]) =>
      h("button", { class: S.filter === id ? "active" : "", onclick: () => { S.filter = id; renderToolbar(); renderList(); } }, label))),
    h("span", { class: "spacer" }),
    search,
    h("button", { class: "btn small", title: "Approve every ready proposal with confidence ≥ 75% and no warnings", onclick: approveConfident }, "Approve confident"));
}

function renderList() {
  const list = $("#plist");
  if (!list) return;
  const items = visibleProposals();
  if (!items.length) {
    list.replaceChildren(h("div", { class: "empty" }, running(S.job.job) ? "Proposals appear here as topics are analysed…" : "Nothing here."));
    return;
  }
  const scroll = list.scrollTop;
  list.replaceChildren(...items.map((p) => {
    const topics = p.topic_ids.length;
    return h("div", { class: `pitem${p.id === S.sel ? " sel" : ""}`, onclick: () => select(p.id) },
      h("div", { class: "t" }, p.title),
      h("div", { class: "d" }, p.action === "update" ? `↳ ${p.target_path}` : folderTitle(p.subject, p.subfolder),
        topics > 1 ? `  ·  ${topics} merged topics` : "",
        p.flags.length ? h("span", { class: "flag" }, `  ·  ⚠ ${p.flags.length}`) : null),
      h("div", { class: "r" },
        badge(p.no_new_information ? "nothing new" : p.status, p.no_new_information ? "rejected" : p.status),
        badge(p.action),
        h("div", { class: "conf", title: `Confidence ${pct(p.confidence)}` }, h("i", { style: `width:${pct(p.confidence)};background:${confColor(p.confidence)}` }))));
  }));
  list.scrollTop = scroll;
}

function select(pid) {
  S.sel = pid;
  S.sub = "preview";
  renderList();
  renderDetail();
}

function selected() { return S.job.proposals.find((p) => p.id === S.sel); }

function maybeRenderDetail() {
  const p = selected();
  const sig = p ? JSON.stringify([p.status, p.content, p.title, p.subject, p.subfolder, p.target_path, p.action, p.flags]) : "";
  const editing = S.sub === "edit" && S.drafts[S.sel] !== undefined;
  if (sig !== S.lastSel && !editing) { delete S.preview[S.sel]; renderDetail(); }
}

function renderDetail() {
  const box = $("#detail");
  if (!box) return;
  const p = selected();
  if (!p) { box.replaceChildren(h("div", { class: "empty" }, "Select a proposal.")); S.lastSel = ""; return; }
  S.lastSel = JSON.stringify([p.status, p.content, p.title, p.subject, p.subfolder, p.target_path, p.action, p.flags]);
  const topics = p.topic_ids.map((id) => S.job.topics.find((t) => t.id === id)).filter(Boolean);
  const first = topics[0] || {};
  const locked = p.status === "applied";
  const working = p.status === "pending" || p.status === "generating";

  const actions = h("div", { class: "actions" },
    h("button", { class: `btn ok${p.status === "approved" ? " on" : ""}`, disabled: locked || !p.content || working, onclick: () => setStatus(p, p.status === "approved" ? "ready" : "approved") }, p.status === "approved" ? "✓ Approved" : "Approve"),
    h("button", { class: `btn bad${p.status === "rejected" ? " on" : ""}`, disabled: locked || working, onclick: () => setStatus(p, p.status === "rejected" ? "ready" : "rejected") }, p.status === "rejected" ? "✗ Rejected" : "Reject"),
    h("button", { class: "btn", disabled: locked || working, onclick: () => regenerate(p) }, "↻ Rewrite"),
    working ? h("span", { class: "muted small" }, "Writing with the local model…") : null,
    locked ? h("span", { class: "muted small" }, `Applied to ${p.applied_path}`) : null);

  const why = h("div", { class: "why" },
    h("div", {}, h("b", {}, p.action === "update" ? "Adds to an existing note. " : "New note. "),
      first.reason || "", " ", h("span", { class: "faint" }, `(confidence ${pct(p.confidence)})`)),
    h("ul", {}, topics.map((t) => h("li", {}, `${t.title}`, h("span", { class: "faint" }, `  · pages ${t.pages || "—"} · ${t.chars} chars` + (t.section ? ` · ${t.section.split(" › ").pop()}` : ""))))),
    first.alternatives && first.alternatives.length && !locked
      ? h("div", { class: "row wrap small", style: "margin-top:8px" }, h("span", { class: "muted" }, "Other folders:"),
          first.alternatives.map((a) => h("button", { class: "btn small ghost", onclick: () => saveDest(p, { action: "create", subject: a.subject, subfolder: a.subfolder }) }, folderTitle(a.subject, a.subfolder))))
      : null,
    p.related && p.related.length
      ? h("div", { class: "small", style: "margin-top:6px" }, h("span", { class: "muted" }, "Related notes: "), p.related.map((r) => r.title).join(" · "))
      : null);

  const subtabs = [["preview", "Preview"], ["edit", "Edit Markdown"], ["changes", "File changes"], ["source", `Source (${topics.length})`]];
  box.replaceChildren(
    h("h3", {}, p.title),
    h("div", { class: "row wrap small" }, badge(p.action), badge(p.status), h("span", { class: "muted" }, p.action === "update" ? p.target_path : folderTitle(p.subject, p.subfolder))),
    destinationEditor(p, locked),
    why,
    p.error ? h("ul", { class: "flags" }, h("li", { style: "background:var(--bad-soft);color:var(--bad)" }, p.error)) : null,
    p.flags.length ? h("ul", { class: "flags" }, p.flags.map((f) => h("li", {}, "⚠ " + f))) : null,
    actions,
    h("div", { class: "subtabs" }, subtabs.map(([id, label]) => h("button", { class: S.sub === id ? "active" : "", onclick: () => { S.sub = id; renderDetail(); } }, label))),
    h("div", { id: "subbody" }));
  renderSub(p, topics, locked, working);
}

function destinationEditor(p, locked) {
  const d = S.dest[p.id] || { action: p.action, subject: p.subject, subfolder: p.subfolder, target_path: p.target_path };
  S.dest[p.id] = d;
  const subjects = subjectsList();
  const subj = subjects.find((s) => s.name === d.subject) || subjects[0];
  const folders = subj ? subj.subfolders : [];
  const folder = folders.find((f) => f.name === d.subfolder) || folders[0];
  if (folder && d.subfolder !== folder.name) d.subfolder = folder.name;
  const notes = (folder && folder.notes) || [];
  const changed = d.action !== p.action || d.subject !== p.subject || d.subfolder !== p.subfolder || (d.action === "update" && d.target_path !== p.target_path);
  const rerender = () => renderDetail();
  return h("div", { class: "dest" },
    h("label", {}, h("span", {}, "Action"),
      h("select", { disabled: locked, onchange: (e) => { d.action = e.target.value; if (d.action === "update" && !notes.some((n) => n.path === d.target_path)) d.target_path = notes[0] && notes[0].path; rerender(); } },
        h("option", { value: "create", selected: d.action === "create" }, "Create a new note"),
        h("option", { value: "update", selected: d.action === "update" }, "Add to an existing note"))),
    h("label", {}, h("span", {}, "Subject"),
      h("select", { disabled: locked, onchange: (e) => { d.subject = e.target.value; d.subfolder = null; d.target_path = null; rerender(); } },
        subjects.map((s) => h("option", { value: s.name, selected: subj && s.name === subj.name }, s.title)))),
    h("label", {}, h("span", {}, "Module folder"),
      h("select", { disabled: locked, onchange: (e) => { d.subfolder = e.target.value; d.target_path = null; rerender(); } },
        folders.map((f) => h("option", { value: f.name, selected: folder && f.name === folder.name }, `${f.title} (${f.count})`)))),
    d.action === "update"
      ? h("label", {}, h("span", {}, "Existing note"),
          h("select", { disabled: locked, onchange: (e) => { d.target_path = e.target.value; rerender(); } },
            notes.map((n) => h("option", { value: n.path, selected: n.path === d.target_path }, n.title))))
      : null,
    changed && !locked
      ? h("div", { style: "align-self:end" }, h("button", { class: "btn primary small", onclick: () => saveDest(p, d) }, d.action !== p.action || d.action === "update" ? "Move & rewrite" : "Move here"))
      : null);
}

async function saveDest(p, d) {
  const body = d.action === "update" ? { action: "update", target_path: d.target_path } : { action: "create", subject: d.subject, subfolder: d.subfolder };
  try {
    await api.patch(`/api/jobs/${S.jobId}/proposals/${p.id}`, body);
    delete S.dest[p.id]; delete S.preview[p.id];
    toast("Destination updated");
    loadJob(false);
  } catch (e) { toast(e.message, true); }
}

async function setStatus(p, status) {
  try {
    await api.patch(`/api/jobs/${S.jobId}/proposals/${p.id}`, { status });
    await loadJob(false);
    if (status === "approved") {  // jump to the next item that still needs a decision
      const next = visibleProposals().find((x) => x.status === "ready" && x.id !== p.id);
      if (next) select(next.id);
    }
  } catch (e) { toast(e.message, true); }
}

async function regenerate(p) {
  try { await api.post(`/api/jobs/${S.jobId}/proposals/${p.id}/regenerate`); delete S.preview[p.id]; toast("Rewriting…"); loadJob(false); }
  catch (e) { toast(e.message, true); }
}

async function approveConfident() {
  try {
    const r = await api.post(`/api/jobs/${S.jobId}/approve`, { min_confidence: 0.75 });
    toast(`Approved ${r.approved.length} proposal(s)`);
    loadJob(false);
  } catch (e) { toast(e.message, true); }
}

async function renderSub(p, topics, locked, working) {
  const body = $("#subbody");
  if (S.sub === "source") {
    body.replaceChildren(h("div", { class: "muted small" }, "Loading source…"));
    const parts = await Promise.all(topics.map((t) => api.get(`/api/jobs/${S.jobId}/segments/${t.id}`).catch(() => null)));
    body.replaceChildren(...parts.filter(Boolean).map((seg) =>
      h("div", {}, h("div", { class: "small muted" }, `${seg.title} · pages ${seg.page_start || "—"}–${seg.page_end || "—"}`),
        h("div", { class: "source md", html: seg.html }))));
    return;
  }
  if (S.sub === "edit") {
    const draft = S.drafts[p.id] !== undefined ? S.drafts[p.id] : p.content || "";
    const ta = h("textarea", { rows: 26, disabled: locked || working }, draft);
    ta.addEventListener("input", () => { S.drafts[p.id] = ta.value; });
    body.replaceChildren(
      h("div", { class: "small muted", style: "margin-bottom:6px" }, p.action === "update" ? "This text is appended under an “Update” heading at the end of the existing note." : "Body of the new note — the title, source line and related links are added automatically."),
      ta,
      h("div", { class: "actions" },
        h("button", { class: "btn primary", disabled: locked || working, onclick: async () => {
          try {
            await api.patch(`/api/jobs/${S.jobId}/proposals/${p.id}`, { content: ta.value, title: p.title });
            delete S.drafts[p.id]; delete S.preview[p.id]; toast("Saved"); S.sub = "preview"; loadJob(false);
          } catch (e) { toast(e.message, true); }
        } }, "Save"),
        h("button", { class: "btn", onclick: () => { delete S.drafts[p.id]; renderDetail(); } }, "Discard changes")));
    return;
  }
  if (!p.content) {
    body.replaceChildren(h("div", { class: "empty" }, working ? "The local model is writing this note…" : p.no_new_information ? "The existing note already covers this source. Reject it, or change the action to create a separate note." : "No content."));
    return;
  }
  let prev = S.preview[p.id];
  if (!prev) {
    body.replaceChildren(h("div", { class: "muted small" }, "Rendering…"));
    try { prev = S.preview[p.id] = await api.get(`/api/jobs/${S.jobId}/proposals/${p.id}/preview`); }
    catch (e) { body.replaceChildren(h("div", { class: "alert bad" }, e.message)); return; }
  }
  if (S.sel !== p.id) return;
  if (prev.error) { body.replaceChildren(h("div", { class: "alert bad" }, prev.error)); return; }
  if (S.sub === "changes") {
    body.replaceChildren(
      h("div", { class: "small muted", style: "margin-bottom:8px" }, `${prev.changes.length} file(s) will change when this proposal is applied.`),
      (prev.warnings || []).map((w) => h("div", { class: "alert" }, w)),
      prev.changes.map((c) => h("details", { class: "filechange", open: c.kind.startsWith("note") },
        h("summary", {}, `${c.new ? "+ new  " : "~ edit "} ${c.path}`, h("span", { class: "faint" }, `  (${c.kind})`)),
        diffView(c.diff))));
    return;
  }
  body.replaceChildren(
    h("div", { class: "small muted", style: "margin-bottom:8px" }, p.action === "update" ? `Appended to ${prev.note_path}:` : `Will be created as ${prev.note_path}`),
    h("div", { class: "md", html: prev.html || "" }));
}

function diffView(text) {
  const pre = h("pre", { class: "diff" });
  for (const line of (text || "").split("\n")) {
    const cls = line.startsWith("+") && !line.startsWith("+++") ? "add" : line.startsWith("-") && !line.startsWith("---") ? "del" : line.startsWith("@@") ? "hunk" : "";
    pre.append(h("span", { class: cls }, line + "\n"));
  }
  return pre;
}

// ---------- skipped topics
function renderSkippedTab() {
  const rows = S.job.topics.filter((t) => t.relevant === false || t.stage === "error");
  if (!rows.length) { $("#jobbody").replaceChildren(h("div", { class: "empty" }, "No topics were skipped.")); return; }
  $("#jobbody").replaceChildren(h("div", { class: "body" },
    h("p", { class: "muted small" }, "Topics the model judged not to be study material (contents pages, adverts, quizzes) or that failed. Include any that were skipped by mistake."),
    h("table", { class: "simple" },
      h("tr", {}, h("th", {}, "Topic"), h("th", {}, "Pages"), h("th", {}, "Why skipped"), h("th", {}, "")),
      rows.map((t) => h("tr", {},
        h("td", {}, t.title), h("td", {}, t.pages || "—"), h("td", { class: "muted" }, t.error || t.skip_reason || ""),
        h("td", {}, h("button", { class: "btn small", disabled: !!(S.job.busy || {})[`${S.jobId}:t${t.id}`], onclick: () => includeTopic(t) }, "Include")))))));
}
async function includeTopic(t) {
  try { await api.post(`/api/jobs/${S.jobId}/topics/${t.id}/include`); toast("Analysing topic…"); loadJob(false); }
  catch (e) { toast(e.message, true); }
}

// ---------- document tab
async function renderDocumentTab() {
  const doc = S.job.document;
  if (!doc) { $("#jobbody").replaceChildren(h("div", { class: "empty" }, "Not extracted yet.")); return; }
  const md = h("pre", { class: "diff", style: "max-height:420px;overflow:auto" }, "Loading…");
  $("#jobbody").replaceChildren(h("div", { class: "body" },
    doc.warnings.map((w) => h("div", { class: "alert", style: "margin:0 0 8px" }, w)),
    h("p", {}, `${doc.kind.toUpperCase()} · ${doc.pages_processed} of ${doc.page_count} page(s) read` + (doc.ocr_pages.length ? ` · OCR on ${doc.ocr_pages.length} page(s)` : "") + ` · heading styles classified by ${doc.style_roles_by || "markup"}`),
    doc.styles.length ? h("table", { class: "simple" },
      h("tr", {}, h("th", {}, "Style"), h("th", {}, "Font"), h("th", {}, "Count"), h("th", {}, "Role"), h("th", {}, "Examples")),
      doc.styles.map((s) => h("tr", {}, h("td", {}, s.id), h("td", {}, `${s.size}pt ${s.bold ? "bold" : ""} ${s.color}`), h("td", {}, s.count), h("td", {}, badge(s.role, s.role === "topic" ? "create" : "")), h("td", { class: "muted small" }, s.examples.slice(0, 3).join(" | "))))) : null,
    h("h3", {}, "Extracted text"), md));
  try { const r = await api.get(`/api/jobs/${S.jobId}/document`); md.textContent = r.markdown; }
  catch (e) { md.textContent = e.message; }
}

// ---------- apply dialog
function openApply() {
  const dlg = $("#dialog");
  const approved = S.job.proposals.filter((p) => p.status === "approved");
  const repo = S.status && S.status.repo;
  const canGit = repo && repo.is_repo;
  const canPr = canGit && repo.remote && repo.gh_available;
  const branch = `notes/${S.job.job.source_id}`;
  const form = h("form", { method: "dialog" },
    h("header", {}, `Apply ${approved.length} approved change(s)`),
    h("div", { class: "body" },
      h("p", { class: "muted" }, "New notes are created in their module folders (with README, MASTER-README and module compilation entries). Updates are appended to the end of existing notes — nothing existing is rewritten. Originals are backed up first."),
      h("ul", { class: "small" }, approved.slice(0, 12).map((p) => h("li", {}, `${p.action === "update" ? "Update" : "New"}: ${p.action === "update" ? p.target_path : folderTitle(p.subject, p.subfolder) + " › " + p.title}`)), approved.length > 12 ? h("li", {}, `…and ${approved.length - 12} more`) : null),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "commit", checked: canGit, disabled: !canGit }), "Commit the changed files to Git ", h("span", { class: "faint small" }, "(only these files — anything else you have staged is untouched)")),
      h("label", { class: "field" }, h("span", {}, "Branch"), h("input", { type: "text", name: "branch", value: branch, disabled: !canGit })),
      h("label", { class: "check" }, h("input", { type: "checkbox", name: "push_pr", disabled: !canPr }), "Push the branch and open a pull request ", h("span", { class: "faint small" }, canPr ? "(uses your GitHub CLI login)" : "(needs a remote and the GitHub CLI)")),
      h("div", { id: "applyresult" })),
    h("footer", {},
      h("button", { class: "btn", value: "cancel", type: "button", onclick: () => dlg.close() }, "Close"),
      h("button", { class: "btn primary", id: "applybtn", type: "button", onclick: () => doApply(form) }, "Apply")));
  dlg.replaceChildren(form);
  dlg.showModal();
}

async function doApply(form) {
  const btn = $("#applybtn");
  btn.disabled = true; btn.textContent = "Applying…";
  const out = $("#applyresult");
  try {
    const r = await api.post(`/api/jobs/${S.jobId}/apply`, {
      commit: form.commit.checked, branch: form.branch.value.trim() || null, push_pr: form.push_pr.checked,
    });
    const errs = Object.entries(r.errors || {});
    out.replaceChildren(
      h("div", { class: "alert info", style: "margin:10px 0 0" }, `Applied ${r.applied.length} proposal(s); wrote ${r.written.length} file(s).`),
      r.commit ? h("div", { class: "small", style: "margin-top:6px" }, `Committed ${r.commit.commit.slice(0, 8)} on ${r.commit.branch}.`) : null,
      r.pr && r.pr.pr_url ? h("div", { class: "small" }, "Pull request: ", h("a", { href: r.pr.pr_url, target: "_blank", rel: "noopener" }, r.pr.pr_url)) : null,
      r.git_error ? h("div", { class: "alert bad", style: "margin:6px 0 0" }, r.git_error) : null,
      errs.map(([pid, msg]) => h("div", { class: "alert bad", style: "margin:6px 0 0" }, `${pid}: ${msg}`)),
      (r.warnings || []).map((w) => h("div", { class: "alert", style: "margin:6px 0 0" }, w)),
      h("details", { class: "small", style: "margin-top:8px" }, h("summary", {}, "Files written"), h("ul", {}, r.written.map((w) => h("li", {}, w)))));
    btn.textContent = "Done";
    S.preview = {};
    loadJob(false);
    loadStatus();
    loadTaxonomy(true);
  } catch (e) {
    out.replaceChildren(h("div", { class: "alert bad", style: "margin:10px 0 0" }, e.message));
    btn.disabled = false; btn.textContent = "Apply";
  }
}

// ------------------------------------------------------------------ library
async function renderLibrary() {
  const app = $("#app");
  app.replaceChildren(h("div", { class: "loading" }, "Loading library…"));
  await loadTaxonomy(true);
  const search = h("input", { type: "search", placeholder: "Search note titles…", style: "max-width:360px" });
  const grid = h("div", { class: "lib" });
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    grid.replaceChildren(...subjectsList().map((s) => {
      const folders = s.subfolders.map((f) => ({ f, notes: q ? f.notes.filter((n) => n.title.toLowerCase().includes(q)) : [] }))
        .filter((x) => !q || x.notes.length || x.f.title.toLowerCase().includes(q));
      if (q && !folders.length) return null;
      const total = s.subfolders.reduce((a, f) => a + f.count, 0);
      return h("section", { class: "panel" },
        h("header", {}, h("h2", {}, s.title), h("span", { class: "spacer" }), h("span", { class: "muted small" }, `${total} notes`)),
        h("div", { class: "body" }, h("ul", {}, folders.map(({ f, notes }) => [
          h("li", {}, h("span", {}, f.title), h("span", { class: "faint" }, String(f.count))),
          notes.map((n) => h("li", { class: "small" }, h("span", { class: "muted" }, "· " + n.title))),
        ]))));
    }).filter(Boolean));
  };
  search.addEventListener("input", draw);
  const total = subjectsList().reduce((a, s) => a + s.subfolders.reduce((b, f) => b + f.count, 0), 0);
  app.replaceChildren(
    h("div", { class: "row", style: "margin-bottom:14px" }, h("h2", { style: "margin:0" }, "Notes library"), h("span", { class: "muted" }, `${subjectsList().length} subjects · ${total} notes`), h("span", { class: "spacer" }), search),
    grid);
  draw();
}

// ------------------------------------------------------------------ boot
(async function boot() {
  await loadStatus();
  route();
  setInterval(loadStatus, 30000);
})();
