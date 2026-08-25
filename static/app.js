const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

function esc(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function parseNums(s) {
  return s.split(",").map((x) => x.trim()).filter(Boolean).map(Number);
}

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

/** Shows a spinner over `wrapEl` (which must have class "img-wrap" and
 * contain a .spinner + the <img>) while `imgEl` loads `src`. Page renders
 * are serialized server-side (see app.py's render lock), so a thumbnail can
 * sit blank for a couple seconds behind others -- without this it looks
 * broken rather than just slow. */
function loadImage(wrapEl, imgEl, src) {
  wrapEl.classList.add("loading");
  const clear = () => wrapEl.classList.remove("loading");
  imgEl.addEventListener("load", clear, { once: true });
  imgEl.addEventListener("error", clear, { once: true });
  imgEl.src = src;
}

/** Toggles a small inline spinner + text on a button while `busy` is true,
 * restoring its original label when false. Safe to call repeatedly. */
function setBtnBusy(btn, busy, busyText) {
  if (busy) {
    if (btn.dataset.idleLabel === undefined) btn.dataset.idleLabel = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner btn-spinner"></span>${esc(busyText || "Working...")}`;
  } else {
    btn.disabled = false;
    btn.textContent = btn.dataset.idleLabel ?? btn.textContent;
    delete btn.dataset.idleLabel;
  }
}

function loadingBlock(text) {
  return `<div class="loading-block"><span class="spinner"></span>${esc(text)}</div>`;
}

// ============================================================ tabs ====

$$(".tab").forEach((btn) => btn.addEventListener("click", () => switchTab(btn.dataset.tab)));

function switchTab(name) {
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  if (name === "run") loadDocs();
  if (name === "review") initReviewTab();
  if (name === "config") loadConfigTab();
  if (name === "tables") loadAuthoredTables();
  if (name === "chunks") initChunksTab();
  if (name === "pages") initPagesTab();
}

/** The one entry point for "go work on this specific handbook" -- used after
 * adding a handbook, replacing its PDF, or clicking a card. Switches to the
 * Review tab scoped to just that doc, optionally running the pipeline first
 * and showing that run's result inline. */
async function enterReview(doc, { run = false } = {}) {
  switchTab("review");
  await populateDocSelect($("#review-doc-select"));
  $("#review-doc-select").value = doc;
  $("#review-run-result").innerHTML = "";
  if (run) {
    await runAndShow(doc, $("#review-run-result"));
  }
  await loadReviewTab();
}

// ==================================================== HANDBOOKS tab ====
// An overview grid: what's here, at a glance, and how to add/remove one.
// Actually running a handbook and clearing its flagged pages happens on the
// separate Review tab -- one handbook at a time, nothing else competing for
// attention on screen.

const REVIEW_REASON_TEXT = {
  table_promised_not_extracted: "The text mentions a table here, but the computer couldn't read it — it's probably a picture of a table, not real table data.",
  no_text_with_image: "This page has no readable text, just an image — it may be a scanned page.",
  complex_diagram: "This page looks like a flowchart or diagram. The extracted text may be jumbled out of order.",
};

async function loadDocs() {
  $("#doc-cards").innerHTML = loadingBlock("Loading handbooks...");
  const docs = await fetchJSON("/api/docs");
  const reviews = await Promise.all(
    docs.map((d) => fetchJSON(`/api/docs/${d.id}/review`).catch(() => []))
  );

  const wrap = $("#doc-cards");
  wrap.innerHTML = "";
  docs.forEach((d, i) => wrap.appendChild(buildDocCard(d, reviews[i])));
}

function buildDocCard(d, review) {
  const pending = review.filter((r) => r.status === "pending" || r.status === "needs_rerun");
  const card = document.createElement("div");
  card.className = "card";

  const pdfBadge = d.pdf_exists ? "" : ' <span class="badge bad">no PDF yet</span>';
  const pageBadge = d.actual_pages == null ? ""
    : d.pages_match ? ""
    : ` <span class="badge bad">page count changed (${d.actual_pages} vs expected ${d.expected_pages})</span>`;
  const reviewLine = pending.length
    ? `<div class="gate-line bad review-open" data-doc="${d.id}" style="cursor:pointer;">${pending.length} page${pending.length > 1 ? "s" : ""} need${pending.length > 1 ? "" : "s"} a quick look &rarr;</div>`
    : d.chunk_count
    ? `<div class="gate-line ok">Nothing needs review.</div>`
    : "";

  card.innerHTML = `
    <div class="card-head">
      <h3>${esc(d.source)}</h3>
      <button type="button" class="small-btn danger remove-btn" data-doc="${d.id}">remove</button>
    </div>
    <dl>
      <dt>PDF</dt><dd>${esc(d.pdf_filename)}${pdfBadge}${pageBadge}</dd>
      <dt>Processed</dt><dd>${d.chunk_count ? `${d.chunk_count} section(s) found` : "not processed yet"}</dd>
    </dl>
    ${reviewLine}
    <div class="upload-row">
      <input type="file" accept="application/pdf" class="pdf-upload" data-doc="${d.id}">
      <button type="button" class="small-btn upload-btn" data-doc="${d.id}">Replace PDF</button>
    </div>
    <div class="card-actions">
      <button type="button" class="primary open-btn" data-doc="${d.id}">${d.chunk_count ? "Open" : "Process this handbook"} &rarr;</button>
    </div>
  `;

  card.querySelector(".remove-btn").addEventListener("click", () => removeHandbook(d.id, d.source));
  card.querySelector(".upload-btn").addEventListener("click", () => uploadPdf(d.id));
  card.querySelector(".open-btn").addEventListener("click", () => enterReview(d.id));
  const reviewOpen = card.querySelector(".review-open");
  if (reviewOpen) reviewOpen.addEventListener("click", () => enterReview(d.id));

  return card;
}

function buildReviewItem(d, r) {
  const item = document.createElement("div");
  const resolved = r.status === "covered" || r.status === "cleared";
  item.className = "review-item" + (resolved ? " cleared" : "");
  const reasonText = r.reasons.map((x) => REVIEW_REASON_TEXT[x] || x).join(" ");
  let statusLine = "";
  if (r.status === "covered") statusLine = `<div class="status-msg ok">Resolved with a manual correction (${esc(r.covering_authored_ids.join(", "))}).</div>`;
  if (r.status === "cleared") statusLine = `<div class="status-msg ok">Marked as fine${r.cleared_note ? ": " + esc(r.cleared_note) : "."}</div>`;
  if (r.status === "needs_rerun") statusLine = `<div class="status-msg bad">A correction was saved for this page, but the results haven't been re-processed yet -- click Process/Re-run above to apply it.</div>`;

  item.innerHTML = `
    <div class="img-wrap">
      <span class="spinner"></span>
      <img alt="page ${r.page}">
    </div>
    <div class="review-body">
      <p><strong>Page ${r.page}</strong></p>
      <p class="review-reason">${esc(reasonText)}</p>
      ${statusLine}
      <div class="review-actions"></div>
    </div>
  `;
  loadImage(item.querySelector(".img-wrap"), item.querySelector("img"), `/api/docs/${d.id}/pages/${r.page}/image`);
  const actionsWrap = item.querySelector(".review-actions");
  if (r.status === "pending" || r.status === "needs_rerun") {
    const fineBtn = document.createElement("button");
    fineBtn.className = "small-btn";
    fineBtn.textContent = "Looks fine";
    fineBtn.addEventListener("click", () => clearReviewItem(d.id, r.page));
    const fixBtn = document.createElement("button");
    fixBtn.className = "small-btn primary";
    fixBtn.textContent = r.status === "needs_rerun" ? "Edit the correction" : "Type in the correct text";
    fixBtn.addEventListener("click", () => startCorrection(d, r.page));
    const viewBtn = document.createElement("button");
    viewBtn.className = "small-btn";
    viewBtn.textContent = "View full page";
    viewBtn.addEventListener("click", () => openPageExplorer(d.id, r.page));
    actionsWrap.append(fineBtn, fixBtn, viewBtn);
  } else {
    const undoBtn = document.createElement("button");
    undoBtn.className = "small-btn";
    undoBtn.textContent = r.status === "covered" ? "Unverify correction" : "Undo";
    undoBtn.addEventListener("click", () => undoReviewItem(d, r));
    actionsWrap.appendChild(undoBtn);
  }
  return item;
}

async function clearReviewItem(doc, page, status = "cleared") {
  const note = status === "cleared" ? (prompt("Optional note (why is this page fine as-is)?") || "") : "";
  try {
    await fetchJSON(`/api/docs/${doc}/review/${page}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note, by: "" }),
    });
    await loadReviewTab();
  } catch (e) {
    alert("couldn't update: " + e.message);
  }
}

/** A "cleared" item (someone said "looks fine") lives only in
 * review_state.json, so undoing it is just clearing that entry. A "covered"
 * item is resolved by a *verified* authored correction, so clearReviewItem
 * alone did nothing for it -- it has to un-verify the correction itself,
 * which is what was actually broken. The transcription stays; only the
 * verified flag drops, putting it back in the pending queue. */
async function undoReviewItem(d, r) {
  try {
    if (r.status === "covered") {
      const tables = await fetchJSON("/api/authored-tables");
      const updated = tables.map((t) =>
        t.doc_id === d.doc_id && r.covering_authored_ids.includes(t.authored_id)
          ? { ...t, verified: false }
          : t
      );
      await fetchJSON("/api/authored-tables", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tables: updated }),
      });
    } else {
      await fetchJSON(`/api/docs/${d.id}/review/${r.page}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "pending", note: "", by: "" }),
      });
    }
    await loadReviewTab();
  } catch (e) {
    alert("couldn't undo: " + e.message);
  }
}

// ------------------------------------------------- inline correction editor ---
// Page-Explorer-style side by side: the actual page image and its raw
// extracted text on the left/top for reference, an editable transcription on
// the right/below -- so fixing a flagged page doesn't mean leaving the
// Review tab to a bare form with no picture of what you're transcribing.

async function startCorrection(d, page) {
  const editor = $("#correction-editor");
  editor.dataset.doc = d.id;
  editor.dataset.docId = d.doc_id;
  editor.dataset.page = page;

  $("#correction-editor-title").textContent = `${d.source} — Page ${page}`;
  loadImage($("#correction-image-wrap"), $("#correction-image"), `/api/docs/${d.id}/pages/${page}/image`);
  $("#correction-extracted-text").textContent = "Loading...";
  $("#correction-status").textContent = "";
  editor.style.display = "block";
  editor.scrollIntoView({ behavior: "smooth", block: "start" });

  const authoredId = `page_${page}`;
  const [pageData, existingTables] = await Promise.all([
    fetchJSON(`/api/docs/${d.id}/pages/${page}/text`).catch(() => ({ text: "" })),
    fetchJSON("/api/authored-tables").catch(() => []),
  ]);
  $("#correction-extracted-text").textContent = pageData.text || "(no extracted text)";

  const existing = existingTables.find((t) => t.doc_id === d.doc_id && t.authored_id === authoredId);
  $("#correction-title").value = existing ? existing.title : `Page ${page} (manual correction)`;
  $("#correction-breadcrumb").value = existing ? existing.breadcrumb : `[${d.source}]`;
  $("#correction-text").value = existing ? existing.text : (pageData.text || "");
  $("#correction-verified").checked = existing ? !!existing.verified : false;
  $("#correction-authored-by").value = existing ? (existing.authored_by || "") : rememberedAuthor();
}

function rememberedAuthor() {
  try { return localStorage.getItem("askwhiz_authored_by") || ""; } catch { return ""; }
}

$("#correction-cancel").addEventListener("click", () => {
  $("#correction-editor").style.display = "none";
});

$("#correction-save").addEventListener("click", saveCorrection);

async function saveCorrection() {
  const editor = $("#correction-editor");
  const doc = editor.dataset.doc;
  const docId = editor.dataset.docId;
  const page = Number(editor.dataset.page);
  const authoredId = `page_${page}`;
  const statusEl = $("#correction-status");

  const text = $("#correction-text").value;
  if (!text.trim()) {
    statusEl.textContent = "Type in the correct text first.";
    statusEl.className = "status-msg bad";
    return;
  }

  const authoredBy = $("#correction-authored-by").value.trim();
  const entry = {
    doc_id: docId,
    authored_id: authoredId,
    title: $("#correction-title").value.trim(),
    breadcrumb: $("#correction-breadcrumb").value.trim(),
    covers_pages: [page],
    pages: [page],
    page_labels: [page],
    verified: $("#correction-verified").checked,
    authored_by: authoredBy,
    authored_on: new Date().toISOString().slice(0, 10),
    provenance: `Manually corrected via the Review tab for page ${page}.`,
    text,
  };
  try { localStorage.setItem("askwhiz_authored_by", authoredBy); } catch { /* ignore */ }

  const saveBtn = $("#correction-save");
  setBtnBusy(saveBtn, true, "Saving...");
  statusEl.textContent = ""; statusEl.className = "status-msg";
  try {
    const existing = await fetchJSON("/api/authored-tables");
    const idx = existing.findIndex((t) => t.doc_id === docId && t.authored_id === authoredId);
    const updated = idx >= 0
      ? existing.map((t, i) => (i === idx ? entry : t))
      : [...existing, entry];
    await fetchJSON("/api/authored-tables", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables: updated }),
    });
    editor.style.display = "none";
    await loadReviewTab();
  } catch (e) {
    statusEl.textContent = "Failed: " + e.message;
    statusEl.className = "status-msg bad";
  } finally {
    setBtnBusy(saveBtn, false);
  }
}

async function openPageExplorer(doc, page) {
  switchTab("pages");
  await populateDocSelect($("#pages-doc-select"));
  $("#pages-doc-select").value = doc;
  await loadPagesTab();
  $("#pages-page-select").value = String(page);
  await loadPageDetail();
}

async function removeHandbook(doc, sourceName) {
  if (!confirm(`Remove "${sourceName}"? This deletes its uploaded PDF (if added here) and everything extracted from it.`)) return;
  try {
    await fetchJSON(`/api/handbooks/${doc}`, { method: "DELETE" });
    await loadDocs();
  } catch (e) {
    alert("couldn't remove: " + e.message);
  }
}

async function uploadPdf(doc) {
  const input = $(`.pdf-upload[data-doc="${doc}"]`);
  if (!input.files.length) { alert("choose a PDF file first"); return; }
  const fd = new FormData();
  fd.append("file", input.files[0]);
  const btn = $(`.upload-btn[data-doc="${doc}"]`);
  btn.disabled = true; btn.textContent = "Uploading...";
  try {
    await fetchJSON(`/api/docs/${doc}/pdf`, { method: "POST", body: fd });
    await enterReview(doc, { run: true });
  } catch (e) {
    alert("upload failed: " + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Replace PDF";
  }
}

$("#new-handbook-add").addEventListener("click", addHandbook);

async function addHandbook() {
  const name = $("#new-handbook-name").value.trim();
  const audience = $("#new-handbook-audience").value.trim();
  const fileInput = $("#new-handbook-file");
  const statusEl = $("#new-handbook-status");

  if (!name) { statusEl.textContent = "Give it a name first."; statusEl.className = "status-msg bad"; return; }
  if (!fileInput.files.length) { statusEl.textContent = "Choose a PDF file first."; statusEl.className = "status-msg bad"; return; }

  const btn = $("#new-handbook-add");
  setBtnBusy(btn, true, "Uploading...");
  statusEl.innerHTML = loadingBlock("Uploading and processing -- this can take a moment...");
  statusEl.className = "status-msg";
  try {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("audience", audience);
    fd.append("file", fileInput.files[0]);
    const created = await fetchJSON("/api/handbooks", { method: "POST", body: fd });

    $("#new-handbook-name").value = "";
    $("#new-handbook-audience").value = "";
    fileInput.value = "";
    statusEl.textContent = "";
    await enterReview(created.id, { run: true });
  } catch (e) {
    statusEl.textContent = "Couldn't add that handbook: " + e.message;
    statusEl.className = "status-msg bad";
  } finally {
    setBtnBusy(btn, false);
  }
}

/** Runs the pipeline for one doc and renders the result into `container`.
 * Shared by the Review tab's Process button and the auto-run after
 * add/replace. */
async function runAndShow(doc, container) {
  container.innerHTML = loadingBlock("Extracting, normalizing, and chunking -- this can take a moment...");
  try {
    const data = await fetchJSON("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docs: [doc], skip_gates: false }),
    });
    renderRunResult(container, data);
    return data;
  } catch (e) {
    container.innerHTML = `<div class="gate-line bad">Something went wrong: ${esc(e.message)}</div>`;
    return null;
  }
}

function renderRunResult(wrap, data) {
  wrap.innerHTML = "";

  const overall = document.createElement("div");
  overall.className = "gate-line " + (data.success ? "ok" : "bad");
  overall.textContent = data.success
    ? "Done — everything checks out."
    : "Done, but something needs your attention — see below.";
  wrap.appendChild(overall);

  for (const g of data.gate1 || []) {
    if (g.ok) continue;
    const el = document.createElement("div");
    el.className = "gate-line bad";
    el.textContent = `${g.doc}: ${g.message || "page count doesn't match what was expected"}`;
    wrap.appendChild(el);
  }

  for (const s of data.stages || []) {
    if (s.ok) continue;
    const details = document.createElement("details");
    details.className = "stage-log fail";
    const summary = document.createElement("summary");
    summary.textContent = `Processing failed — ${s.doc} / ${s.stage} (technical details)`;
    details.appendChild(summary);
    const pre = document.createElement("pre");
    pre.style.margin = "0";
    pre.textContent = (s.stdout || "") + (s.stderr ? "\n--- stderr ---\n" + s.stderr : "");
    details.appendChild(pre);
    wrap.appendChild(details);
  }

  if (data.gate2 && !data.gate2.ok) {
    const el = document.createElement("div");
    el.className = "gate-line bad";
    el.textContent = data.gate2.message;
    wrap.appendChild(el);
  }

  for (const g of data.gate3 || []) {
    if (g.ok) continue;
    const el = document.createElement("div");
    el.className = "gate-line bad";
    el.textContent = `${g.pending.length} page(s) need review — see below.`;
    wrap.appendChild(el);
  }
}

// ======================================================== REVIEW tab ====
// A focused, single-handbook workspace: process it, see what needs a human
// look, and clear those items -- without every other handbook's cards and
// numbers competing for attention on the same screen.

$("#review-doc-select").addEventListener("change", loadReviewTab);
$("#review-run-btn").addEventListener("click", async () => {
  const doc = $("#review-doc-select").value;
  if (!doc) return;
  const btn = $("#review-run-btn");
  setBtnBusy(btn, true, "Processing...");
  await runAndShow(doc, $("#review-run-result"));
  setBtnBusy(btn, false);
  await loadReviewTab(); // sets the button's label (Process vs Re-run) based on the fresh chunk count
});

async function initReviewTab() {
  await populateDocSelect($("#review-doc-select"));
  await loadReviewTab();
}

// switchTab("review") fires initReviewTab() in the background without
// awaiting it, and enterReview() (used by "Open", upload, add-handbook) also
// calls loadReviewTab() directly right after -- so two calls for two
// different docs can be in flight together. Whichever call this guard was
// looking at when it started is compared against the latest call on
// completion, so a slower, now-irrelevant response can never overwrite what
// a newer one already rendered (same pattern as loadPageDetail's).
let reviewTabRequestId = 0;

async function loadReviewTab() {
  const doc = $("#review-doc-select").value;
  const runBtn = $("#review-run-btn");
  if (!doc) {
    $("#review-summary").innerHTML = '<p class="hint">Add a handbook on the Handbooks tab first.</p>';
    $("#review-list").innerHTML = "";
    runBtn.disabled = true;
    return;
  }
  runBtn.disabled = false;
  $("#review-list").innerHTML = loadingBlock("Checking for pages that need review...");

  const requestId = ++reviewTabRequestId;
  const [docs, review] = await Promise.all([
    fetchJSON("/api/docs"),
    fetchJSON(`/api/docs/${doc}/review`),
  ]);
  if (requestId !== reviewTabRequestId) return;
  const d = docs.find((x) => x.id === doc);
  if (!d) return;

  runBtn.textContent = d.chunk_count ? "Re-run" : "Process";

  const pdfBadge = d.pdf_exists ? "" : ' <span class="badge bad">no PDF yet</span>';
  const pageBadge = d.actual_pages == null ? ""
    : d.pages_match ? ""
    : ` <span class="badge bad">page count changed (${d.actual_pages} vs expected ${d.expected_pages})</span>`;
  $("#review-summary").innerHTML = `
    <dl>
      <dt>PDF</dt><dd>${esc(d.pdf_filename)}${pdfBadge}${pageBadge}</dd>
      <dt>Processed</dt><dd>${d.chunk_count ? `${d.chunk_count} section(s) found` : "not processed yet"}</dd>
    </dl>
  `;

  const wrap = $("#review-list");
  wrap.innerHTML = "";
  if (!d.chunk_count) {
    wrap.innerHTML = '<p class="hint">Not processed yet -- click Process above.</p>';
  } else if (!review.length) {
    wrap.innerHTML = '<div class="gate-line ok">Everything looks good — nothing needs review.</div>';
  } else {
    const pending = review.filter((r) => r.status === "pending" || r.status === "needs_rerun");
    const summary = document.createElement("div");
    summary.className = "gate-line " + (pending.length ? "bad" : "ok");
    summary.textContent = pending.length
      ? `${pending.length} page${pending.length > 1 ? "s" : ""} need${pending.length > 1 ? "" : "s"} a quick look.`
      : "All flagged pages have been resolved.";
    wrap.appendChild(summary);
    review.forEach((r) => wrap.appendChild(buildReviewItem(d, r)));
  }
}

// ======================================================= CONFIG tab ====

let fullConfig = null;

async function loadConfigTab() {
  fullConfig = await fetchJSON("/api/config");
  const sel = $("#config-doc-select");
  const prev = sel.value;
  sel.innerHTML = Object.entries(fullConfig.docs)
    .map(([id, d]) => `<option value="${id}">${esc(d.source)}</option>`).join("");
  if (fullConfig.docs[prev]) sel.value = prev;
  if (!sel.dataset.wired) {
    sel.addEventListener("change", () => renderConfigForm(sel.value));
    sel.dataset.wired = "1";
  }
  renderConfigForm(sel.value || Object.keys(fullConfig.docs)[0]);
}

function renderConfigForm(doc) {
  const d = fullConfig.docs[doc];
  const form = $("#config-form");
  form.innerHTML = `
    <fieldset>
      <legend>General</legend>
      <div class="field"><label>doc_id (fixed, used in chunk_id / filenames)</label>
        <input type="text" id="f-doc_id" value="${esc(d.doc_id)}" disabled></div>
      <div class="field"><label>Source title</label><input type="text" id="f-source" value="${esc(d.source)}"></div>
      <div class="field"><label>Audience</label><input type="text" id="f-audience" value="${esc(d.audience)}"></div>
      <div class="field"><label>PDF filename (relative to project root)</label>
        <input type="text" id="f-pdf_filename" value="${esc(d.pdf_filename)}"></div>
      <div class="field-inline">
        <div class="field"><label>page_label_offset</label>
          <input type="number" id="f-page_label_offset" value="${d.page_label_offset}"></div>
        <div class="field"><label>expected_pages_in (Gate 1 drift check)</label>
          <input type="number" id="f-expected_pages_in" value="${d.expected_pages_in}"></div>
      </div>
      <div class="field"><label>suppress_pages (comma-separated PDF page numbers to drop, e.g. cover/TOC)</label>
        <input type="text" id="f-suppress_pages" value="${d.suppress_pages.join(", ")}"></div>
    </fieldset>

    <fieldset>
      <legend>Normalize stage</legend>
      <div class="field">
        <label>strip_line_patterns (regexes; a line fully matching one is dropped, e.g. running header)</label>
        <div id="strip-list"></div>
        <button type="button" id="strip-add" class="small-btn">+ add pattern</button>
      </div>
      <div class="field">
        <label>typos (page number &rarr; wrong/right text pairs)</label>
        <div id="norm-typos-list"></div>
        <button type="button" id="norm-typo-add" class="small-btn">+ add typo</button>
      </div>
    </fieldset>

    <fieldset>
      <legend>Chunker stage (heading detection)</legend>
      <div class="field"><label>footer_pattern (regex; page furniture dropped before heading detection)</label>
        <input type="text" id="f-footer_pattern" value="${esc(d.chunker.footer_pattern)}"></div>
      <div class="field">
        <label><input type="checkbox" id="f-h1-enabled" ${d.chunker.h1_x0 ? "checked" : ""}>
          h1_x0 range set &mdash; also treat bold text in this left-margin x0 range as an H1, not just larger font size</label>
        <div class="field-inline">
          <input type="number" step="0.1" id="f-h1-min" placeholder="min x0" value="${d.chunker.h1_x0 ? d.chunker.h1_x0[0] : ""}">
          <input type="number" step="0.1" id="f-h1-max" placeholder="max x0" value="${d.chunker.h1_x0 ? d.chunker.h1_x0[1] : ""}">
        </div>
      </div>
      <div class="field"><label>missing_tables (comma-separated pages where a table exists on the page image but
        didn't extract as text &mdash; sections touching these that promise a table fail Gate 2 unless covered by an authored table)</label>
        <input type="text" id="f-missing_tables" value="${d.chunker.missing_tables.join(", ")}"></div>
      <div class="field">
        <label>typos (page number &rarr; wrong/right text pairs)</label>
        <div id="chunk-typos-list"></div>
        <button type="button" id="chunk-typo-add" class="small-btn">+ add typo</button>
      </div>
    </fieldset>
  `;

  renderStripList(d.normalize.strip_line_patterns);
  renderTypoList("norm-typos-list", d.normalize.typos);
  renderTypoList("chunk-typos-list", d.chunker.typos);

  $("#strip-add").addEventListener("click", () => addStripRow(""));
  $("#norm-typo-add").addEventListener("click", () => addTypoRow("norm-typos-list", "", "", ""));
  $("#chunk-typo-add").addEventListener("click", () => addTypoRow("chunk-typos-list", "", "", ""));
}

function renderStripList(patterns) {
  $("#strip-list").innerHTML = "";
  patterns.forEach(addStripRow);
}

function addStripRow(value) {
  const wrap = $("#strip-list");
  const row = document.createElement("div");
  row.className = "strip-row";
  row.innerHTML = `<input type="text" class="strip-input" value="${esc(value)}">
    <button type="button" class="small-btn remove">remove</button>`;
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  wrap.appendChild(row);
}

function renderTypoList(listId, typosObj) {
  const wrap = $(`#${listId}`);
  wrap.innerHTML = "";
  for (const [page, fixes] of Object.entries(typosObj)) {
    for (const [wrong, right] of fixes) addTypoRow(listId, page, wrong, right);
  }
}

function addTypoRow(listId, page, wrong, right) {
  const wrap = $(`#${listId}`);
  const row = document.createElement("div");
  row.className = "typo-row";
  row.innerHTML = `
    <input type="number" class="typo-page" placeholder="page" value="${esc(page)}">
    <input type="text" class="typo-wrong" placeholder="wrong text" value="${esc(wrong)}">
    <input type="text" class="typo-right" placeholder="right text" value="${esc(right)}">
    <button type="button" class="small-btn remove">remove</button>
  `;
  row.querySelector(".remove").addEventListener("click", () => row.remove());
  wrap.appendChild(row);
}

function collectStrip() {
  return $$("#strip-list .strip-input").map((i) => i.value.trim()).filter(Boolean);
}

function collectTypos(listId) {
  const out = {};
  $$(`#${listId} .typo-row`).forEach((row) => {
    const page = row.querySelector(".typo-page").value.trim();
    const wrong = row.querySelector(".typo-wrong").value;
    const right = row.querySelector(".typo-right").value;
    if (!page || !wrong) return;
    (out[page] = out[page] || []).push([wrong, right]);
  });
  return out;
}

$("#config-save").addEventListener("click", saveConfig);

async function saveConfig() {
  const doc = $("#config-doc-select").value;
  const d = fullConfig.docs[doc];
  const updated = {
    ...d,
    source: $("#f-source").value,
    audience: $("#f-audience").value,
    pdf_filename: $("#f-pdf_filename").value,
    page_label_offset: Number($("#f-page_label_offset").value),
    expected_pages_in: Number($("#f-expected_pages_in").value),
    suppress_pages: parseNums($("#f-suppress_pages").value),
    normalize: {
      strip_line_patterns: collectStrip(),
      typos: collectTypos("norm-typos-list"),
    },
    chunker: {
      footer_pattern: $("#f-footer_pattern").value,
      h1_x0: $("#f-h1-enabled").checked ? [Number($("#f-h1-min").value), Number($("#f-h1-max").value)] : null,
      missing_tables: parseNums($("#f-missing_tables").value),
      typos: collectTypos("chunk-typos-list"),
    },
  };

  const statusEl = $("#config-status");
  statusEl.textContent = "Saving..."; statusEl.className = "status-msg";
  try {
    const saved = await fetchJSON(`/api/docs/${doc}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: updated }),
    });
    fullConfig.docs[doc] = saved;
    statusEl.textContent = "Saved."; statusEl.className = "status-msg ok";
  } catch (e) {
    statusEl.textContent = "Failed: " + e.message; statusEl.className = "status-msg bad";
  }
}

// ======================================================= TABLES tab ====

async function loadAuthoredTables() {
  const tables = await fetchJSON("/api/authored-tables");
  renderAuthoredTables(tables);
}

function renderAuthoredTables(tables) {
  const wrap = $("#authored-list");
  wrap.innerHTML = "";
  tables.forEach((t) => wrap.appendChild(buildAuthoredCard(t)));
}

function buildAuthoredCard(t) {
  const card = document.createElement("div");
  card.className = "authored-card";
  card.innerHTML = `
    <div class="row">
      <div class="field"><label>doc_id</label><input type="text" class="a-doc_id" value="${esc(t.doc_id || "")}"></div>
      <div class="field"><label>authored_id</label><input type="text" class="a-authored_id" value="${esc(t.authored_id || "")}"></div>
      <label style="align-self:flex-end;"><input type="checkbox" class="a-verified" ${t.verified ? "checked" : ""}> verified</label>
      <button type="button" class="small-btn danger remove-authored" style="align-self:flex-end;">remove</button>
    </div>
    <div class="authored-grid">
      <div class="field"><label>title</label><input type="text" class="a-title" value="${esc(t.title || "")}"></div>
      <div class="field"><label>breadcrumb</label><input type="text" class="a-breadcrumb" value="${esc(t.breadcrumb || "")}"></div>
      <div class="field"><label>covers_pages (comma-separated)</label><input type="text" class="a-covers_pages" value="${(t.covers_pages || []).join(", ")}"></div>
      <div class="field"><label>pages (comma-separated)</label><input type="text" class="a-pages" value="${(t.pages || []).join(", ")}"></div>
      <div class="field"><label>page_labels (comma-separated)</label><input type="text" class="a-page_labels" value="${(t.page_labels || []).join(", ")}"></div>
      <div class="field"><label>authored_by</label><input type="text" class="a-authored_by" value="${esc(t.authored_by || "")}"></div>
      <div class="field"><label>authored_on (YYYY-MM-DD)</label><input type="text" class="a-authored_on" value="${esc(t.authored_on || "")}"></div>
    </div>
    <div class="field"><label>provenance (how/why this was hand-transcribed, and how it was verified)</label>
      <textarea class="a-provenance" rows="3">${esc(t.provenance || "")}</textarea></div>
    <div class="field"><label>text (the transcription itself, prepended with the breadcrumb at chunk time)</label>
      <textarea class="a-text" rows="6">${esc(t.text || "")}</textarea></div>
  `;
  card.querySelector(".remove-authored").addEventListener("click", () => card.remove());
  return card;
}

$("#table-add").addEventListener("click", () => {
  $("#authored-list").appendChild(buildAuthoredCard({ verified: false }));
});

$("#tables-save").addEventListener("click", saveAuthoredTables);

function collectAuthoredTables() {
  return $$(".authored-card").map((card) => ({
    doc_id: card.querySelector(".a-doc_id").value.trim(),
    authored_id: card.querySelector(".a-authored_id").value.trim(),
    title: card.querySelector(".a-title").value,
    breadcrumb: card.querySelector(".a-breadcrumb").value,
    covers_pages: parseNums(card.querySelector(".a-covers_pages").value),
    pages: parseNums(card.querySelector(".a-pages").value),
    page_labels: parseNums(card.querySelector(".a-page_labels").value),
    verified: card.querySelector(".a-verified").checked,
    authored_by: card.querySelector(".a-authored_by").value,
    authored_on: card.querySelector(".a-authored_on").value,
    provenance: card.querySelector(".a-provenance").value,
    text: card.querySelector(".a-text").value,
  }));
}

async function saveAuthoredTables() {
  const statusEl = $("#tables-status");
  statusEl.textContent = "Saving..."; statusEl.className = "status-msg";
  try {
    const tables = collectAuthoredTables();
    const saved = await fetchJSON("/api/authored-tables", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables }),
    });
    renderAuthoredTables(saved);
    statusEl.textContent = "Saved."; statusEl.className = "status-msg ok";
  } catch (e) {
    statusEl.textContent = "Failed: " + e.message; statusEl.className = "status-msg bad";
  }
}

// ======================================================= CHUNKS tab ====

$("#chunks-doc-select").addEventListener("change", loadChunks);
$("#chunks-search").addEventListener("input", debounce(loadChunks, 250));
$("#chunks-download").addEventListener("click", () => {
  window.open(`/api/chunks/${$("#chunks-doc-select").value}/download`, "_blank");
});

async function populateDocSelect(sel) {
  const docs = await fetchJSON("/api/docs");
  const prev = sel.value;
  sel.innerHTML = docs.map((d) => `<option value="${d.id}">${esc(d.source)}</option>`).join("");
  if (docs.some((d) => d.id === prev)) sel.value = prev;
  return docs;
}

async function initChunksTab() {
  await populateDocSelect($("#chunks-doc-select"));
  await loadChunks();
}

async function loadChunks() {
  const doc = $("#chunks-doc-select").value;
  if (!doc) { $("#chunks-list").innerHTML = ""; $("#chunks-count").textContent = ""; return; }
  $("#chunks-list").innerHTML = loadingBlock("Loading...");
  const q = $("#chunks-search").value.trim();
  const url = `/api/chunks/${doc}` + (q ? `?q=${encodeURIComponent(q)}` : "");
  const chunks = await fetchJSON(url);
  $("#chunks-count").textContent = `${chunks.length} chunk(s)`;
  const wrap = $("#chunks-list");
  wrap.innerHTML = "";
  chunks.forEach((c) => {
    const item = document.createElement("div");
    item.className = "chunk-item";
    const shortTag = c.short_section ? '<span class="tag">short</span>' : "";
    const authoredTag = c.source_type === "table_transcription" ? '<span class="tag">authored</span>' : "";
    item.innerHTML = `
      <div class="chunk-head">
        <span class="title">${esc(c.title)}${shortTag}${authoredTag}</span>
        <span class="meta">pages ${JSON.stringify(c.pages)} &middot; ${c.token_count} tok</span>
      </div>
      <div class="chunk-body"></div>
    `;
    item.querySelector(".chunk-body").textContent = c.chunk_text;
    item.querySelector(".chunk-head").addEventListener("click", () => item.classList.toggle("open"));
    wrap.appendChild(item);
  });
}

// ======================================================== PAGES tab ====

$("#pages-doc-select").addEventListener("change", loadPagesTab);
$("#pages-page-select").addEventListener("change", loadPageDetail);

async function initPagesTab() {
  await populateDocSelect($("#pages-doc-select"));
  await loadPagesTab();
}

async function loadPagesTab() {
  const doc = $("#pages-doc-select").value;
  if (!doc) return;
  const pages = await fetchJSON(`/api/docs/${doc}/pages`);
  const sel = $("#pages-page-select");
  const prevPage = sel.value;
  sel.innerHTML = pages.map((p) => {
    const flags = [p.suppressed && "suppressed", p.missing_table && "missing table"].filter(Boolean).join(", ");
    return `<option value="${p.page}">${p.page}${flags ? ` (${flags})` : ""}</option>`;
  }).join("");
  if (pages.some((p) => String(p.page) === prevPage)) sel.value = prevPage;

  if (pages.length) {
    await loadPageDetail();
  } else {
    $("#page-image-wrap").classList.remove("loading");
    $("#page-image").src = "";
    $("#page-text").textContent = "";
    $("#page-spans").innerHTML = "";
    $("#pages-flags").textContent = "no extracted pages yet -- run the pipeline first";
  }
}

// Bumped on every call and captured per-request so a slow, now-superseded
// response (e.g. an in-flight fetch for the page that was showing before the
// user picked a new one) can never overwrite what's on screen after a newer
// request has already resolved.
let pageDetailRequestId = 0;

async function loadPageDetail() {
  const doc = $("#pages-doc-select").value;
  const n = $("#pages-page-select").value;
  if (!n) return;
  const requestId = ++pageDetailRequestId;
  loadImage($("#page-image-wrap"), $("#page-image"), `/api/docs/${doc}/pages/${n}/image`);
  $("#pages-flags").textContent = "";
  $("#page-text").textContent = "Loading...";
  $("#page-spans").innerHTML = loadingBlock("Loading...");
  const data = await fetchJSON(`/api/docs/${doc}/pages/${n}/text`);
  if (requestId !== pageDetailRequestId) return;
  $("#page-text").textContent = data.text;
  const rows = data.spans.map((s) => `
    <tr class="${s.bold ? "span-bold" : ""}">
      <td>${s.size ?? ""}</td><td>${s.x0}</td><td>${s.wrapped ? "↵" : ""}</td><td>${esc(s.text)}</td>
    </tr>`).join("");
  $("#page-spans").innerHTML = `<table>
    <thead><tr><th>size</th><th>x0</th><th>wrap</th><th>text</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// ============================================================ init ====

loadDocs();
