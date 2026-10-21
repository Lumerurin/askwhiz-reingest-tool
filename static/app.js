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
  if (name === "index") loadIndexTab();
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
  const pending = review.filter((r) => r.status === "pending" || r.status === "needs_rerun" || r.status === "unverified");
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
  const reasonText = r.reasons.length
    ? r.reasons.map((x) => REVIEW_REASON_TEXT[x] || x).join(" ")
    : "Manually corrected -- this page wasn't flagged automatically.";
  let statusLine = "";
  if (r.status === "covered") statusLine = `<div class="status-msg ok">Resolved with a manual correction (${esc(r.covering_authored_ids.join(", "))}).</div>`;
  if (r.status === "cleared") statusLine = `<div class="status-msg ok">Marked as fine${r.cleared_note ? ": " + esc(r.cleared_note) : "."}</div>`;
  if (r.status === "needs_rerun") statusLine = `<div class="status-msg bad">A correction was saved for this page, but the results haven't been re-processed yet -- click Process/Re-run above to apply it.</div>`;
  if (r.status === "unverified") statusLine = `<div class="status-msg bad">A correction was started here (${esc(r.covering_authored_ids.join(", "))}) but hasn't been verified yet.</div>`;

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
  // "pending" only ever happens for an auto-flagged page (a manual page
  // always has some authored entry touching it, so it's never "nothing done
  // at all"). "needs_rerun" can happen for EITHER an auto-flagged page that
  // now has a correction, or a manually-corrected page that was never
  // flagged -- "Looks fine" only makes sense for the former, since the
  // latter has no auto-detected flag to dismiss in the first place.
  const wasFlagged = r.reasons.length > 0;
  if (r.status === "pending" || (r.status === "needs_rerun" && wasFlagged)) {
    const fineBtn = document.createElement("button");
    fineBtn.className = "small-btn";
    fineBtn.textContent = "Looks fine";
    fineBtn.addEventListener("click", () => clearReviewItem(d.id, r.page));
    actionsWrap.appendChild(fineBtn);
  }
  if (r.status === "pending" || r.status === "needs_rerun") {
    const fixBtn = document.createElement("button");
    fixBtn.className = "small-btn primary";
    fixBtn.textContent = r.status === "needs_rerun" ? "Edit the correction" : "Fix this page";
    fixBtn.addEventListener("click", () => openPageExplorer(d.id, r.page, { openCorrection: true }));
    actionsWrap.appendChild(fixBtn);
  } else if (r.status === "unverified") {
    // No "Looks fine" here -- there's no auto-detected flag to dismiss, just
    // an unfinished draft that needs finishing.
    const finishBtn = document.createElement("button");
    finishBtn.className = "small-btn primary";
    finishBtn.textContent = "Finish & verify";
    finishBtn.addEventListener("click", () => openPageExplorer(d.id, r.page, { openCorrection: true }));
    actionsWrap.appendChild(finishBtn);
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

function rememberedAuthor() {
  try { return localStorage.getItem("askwhiz_authored_by") || ""; } catch { return ""; }
}

/** Jumps to Page Explorer with a specific handbook/page selected -- used by
 * both a manual "browse to a page" flow and the Review queue's "Fix this
 * page" shortcut. `openCorrection` additionally expands and populates the
 * correction editor built into that tab, so the deep link lands the user
 * ready to type, not just looking. */
async function openPageExplorer(doc, page, { openCorrection = false } = {}) {
  switchTab("pages");
  await populateDocSelect($("#pages-doc-select"));
  $("#pages-doc-select").value = doc;
  await loadPagesTab();
  $("#pages-page-select").value = String(page);
  updatePagesPosition();
  await loadPageDetail();
  if (openCorrection) {
    const details = $("#correction-details");
    details.open = true;
    await loadCorrectionFields();
    details.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
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
    const pending = review.filter((r) => r.status === "pending" || r.status === "needs_rerun" || r.status === "unverified");
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
  // Keep the full original object -- including any field this form has no
  // input for (verified_by, verification_notes, supersedes, anything added
  // later) -- so saving doesn't silently discard it. Previously
  // collectAuthoredTables() rebuilt every card from scratch using only the
  // known field names, which meant clicking "Save all" quietly stripped
  // extra metadata off *every* entry on screen, not just the one you meant
  // to edit.
  card.authoredData = t;
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
    ...(card.authoredData || {}),
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
$("#pages-page-select").addEventListener("change", () => { updatePagesPosition(); loadPageDetail(); });
$("#pages-prev").addEventListener("click", () => stepPage(-1));
$("#pages-next").addEventListener("click", () => stepPage(1));

/** Moves the Page Explorer selection by `delta` options (±1) and loads it.
 * Wired to the Prev/Next buttons and to the left/right arrow keys. */
function stepPage(delta) {
  const sel = $("#pages-page-select");
  const i = sel.selectedIndex + delta;
  if (i < 0 || i >= sel.options.length) return;
  sel.selectedIndex = i;
  loadPageDetail();
  updatePagesPosition();
}

// ---- search within the extracted-text pane (Page Explorer) ----
let currentPageText = "";
let searchMatches = []; // [start, end] offsets into currentPageText
let searchActiveIndex = -1;

/** Re-renders #page-text from currentPageText, highlighting matches for
 * whatever's typed in #page-text-search (case-insensitive substring). Kept
 * as one function so both a fresh page load and a search keystroke can
 * call it without duplicating the highlight-building logic. */
function renderPageText() {
  const el = $("#page-text");
  const q = $("#page-text-search").value.trim();
  if (!q) {
    el.textContent = currentPageText;
    searchMatches = [];
    searchActiveIndex = -1;
    updateSearchCount();
    return;
  }

  const lowerText = currentPageText.toLowerCase();
  const lowerQ = q.toLowerCase();
  searchMatches = [];
  let from = 0;
  while (true) {
    const at = lowerText.indexOf(lowerQ, from);
    if (at === -1) break;
    searchMatches.push([at, at + q.length]);
    from = at + q.length;
  }
  if (searchActiveIndex < 0 && searchMatches.length) searchActiveIndex = 0;
  if (searchActiveIndex >= searchMatches.length) searchActiveIndex = searchMatches.length - 1;

  let html = "";
  let last = 0;
  searchMatches.forEach(([start, end], i) => {
    html += esc(currentPageText.slice(last, start));
    const cls = i === searchActiveIndex ? "search-hit search-hit-active" : "search-hit";
    html += `<mark class="${cls}">${esc(currentPageText.slice(start, end))}</mark>`;
    last = end;
  });
  html += esc(currentPageText.slice(last));
  el.innerHTML = html;

  updateSearchCount();
  const active = $(".search-hit-active", el);
  if (active) active.scrollIntoView({ block: "center" });
}

function updateSearchCount() {
  const q = $("#page-text-search").value.trim();
  $("#page-text-search-count").textContent = !q ? ""
    : searchMatches.length ? `${searchActiveIndex + 1} of ${searchMatches.length}`
    : "no matches";
}

function stepSearchMatch(delta) {
  if (!searchMatches.length) return;
  searchActiveIndex = (searchActiveIndex + delta + searchMatches.length) % searchMatches.length;
  renderPageText();
}

$("#page-text-search").addEventListener("input", debounce(() => {
  searchActiveIndex = 0;
  renderPageText();
}, 120));
$("#page-text-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); stepSearchMatch(e.shiftKey ? -1 : 1); }
  else if (e.key === "Escape" && e.target.value) { e.target.value = ""; renderPageText(); }
});
$("#page-text-search-next").addEventListener("click", () => stepSearchMatch(1));
$("#page-text-search-prev").addEventListener("click", () => stepSearchMatch(-1));

// ---- search within the (editable) correction textarea ----
// A textarea can't render inline <mark> highlights the way the read-only
// #page-text pane does, so this jumps the native text selection to each
// match instead -- same idea as Ctrl+F in a plain text editor. Matches are
// only recomputed here, not auto-jumped-to, on every keystroke in the
// search box; jumping (which moves focus into the textarea to show the
// selection) only happens on Enter or a Prev/Next click, so typing a
// multi-character search term doesn't get interrupted by focus hopping
// away after the first character.
let correctionSearchMatches = [];
let correctionSearchActiveIndex = -1;

function computeCorrectionSearchMatches() {
  const q = $("#correction-text-search").value.trim();
  correctionSearchMatches = [];
  if (!q) return;
  const lower = $("#correction-text").value.toLowerCase();
  const lowerQ = q.toLowerCase();
  let from = 0;
  while (true) {
    const at = lower.indexOf(lowerQ, from);
    if (at === -1) break;
    correctionSearchMatches.push([at, at + q.length]);
    from = at + q.length;
  }
}

function updateCorrectionSearchCount() {
  const q = $("#correction-text-search").value.trim();
  let text = "";
  if (q) {
    if (!correctionSearchMatches.length) text = "no matches";
    else if (correctionSearchActiveIndex < 0) {
      text = correctionSearchMatches.length === 1 ? "1 match" : `${correctionSearchMatches.length} matches`;
    } else {
      text = `${correctionSearchActiveIndex + 1} of ${correctionSearchMatches.length}`;
    }
  }
  $("#correction-text-search-count").textContent = text;
}

function goToCorrectionMatch(index) {
  if (!correctionSearchMatches.length) { updateCorrectionSearchCount(); return; }
  correctionSearchActiveIndex =
    ((index % correctionSearchMatches.length) + correctionSearchMatches.length) % correctionSearchMatches.length;
  const [start, end] = correctionSearchMatches[correctionSearchActiveIndex];
  const textarea = $("#correction-text");
  textarea.focus();
  textarea.setSelectionRange(start, end);
  updateCorrectionSearchCount();
}

function stepCorrectionSearchMatch(delta) {
  if (!correctionSearchMatches.length) computeCorrectionSearchMatches();
  goToCorrectionMatch(correctionSearchActiveIndex + delta);
}

/** Clears the correction-text search box/state -- called whenever the
 * textarea's content is replaced wholesale (a new page loaded, or "use
 * checked pieces" rebuilding it), since stale match offsets from the old
 * text would otherwise point at the wrong place in the new text. */
function resetCorrectionSearch() {
  $("#correction-text-search").value = "";
  correctionSearchMatches = [];
  correctionSearchActiveIndex = -1;
  updateCorrectionSearchCount();
}

$("#correction-text-search").addEventListener("input", debounce(() => {
  computeCorrectionSearchMatches();
  correctionSearchActiveIndex = -1;
  updateCorrectionSearchCount();
}, 120));
$("#correction-text-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); stepCorrectionSearchMatch(e.shiftKey ? -1 : 1); }
  else if (e.key === "Escape" && e.target.value) { e.target.value = ""; correctionSearchMatches = []; correctionSearchActiveIndex = -1; updateCorrectionSearchCount(); }
});
$("#correction-text-search-next").addEventListener("click", () => stepCorrectionSearchMatch(1));
$("#correction-text-search-prev").addEventListener("click", () => stepCorrectionSearchMatch(-1));

function updatePagesPosition() {
  const sel = $("#pages-page-select");
  const n = sel.options.length;
  $("#pages-position").textContent = n ? `page ${sel.selectedIndex + 1} of ${n}` : "";
  $("#pages-prev").disabled = sel.selectedIndex <= 0;
  $("#pages-next").disabled = sel.selectedIndex >= n - 1;
}

// Left/Right arrow keys flip pages while the Pages tab is active, unless
// the user is typing somewhere (an input/textarea/select, or with a
// modifier key held for browser/OS shortcuts).
document.addEventListener("keydown", (e) => {
  if (!$("#tab-pages").classList.contains("active")) return;
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;
  if (e.key === "ArrowLeft") { e.preventDefault(); stepPage(-1); }
  else if (e.key === "ArrowRight") { e.preventDefault(); stepPage(1); }
});
$("#correction-details").addEventListener("toggle", (e) => {
  if (e.target.open) loadCorrectionFields();
});
$("#correction-save").addEventListener("click", saveCorrection);
$("#supersede-use").addEventListener("click", useCheckedAsStartingPoint);

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
  updatePagesPosition();

  if (pages.length) {
    await loadPageDetail();
  } else {
    $("#page-image-wrap").classList.remove("loading");
    $("#page-image").src = "";
    currentPageText = "";
    renderPageText();
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
  $("#correction-status").textContent = "";
  const data = await fetchJSON(`/api/docs/${doc}/pages/${n}/text`);
  if (requestId !== pageDetailRequestId) return;
  currentPageText = data.text;
  renderPageText();
  const rows = data.spans.map((s) => `
    <tr class="${s.bold ? "span-bold" : ""}">
      <td>${s.size ?? ""}</td><td>${s.x0}</td><td>${s.wrapped ? "↵" : ""}</td><td>${esc(s.text)}</td>
    </tr>`).join("");
  $("#page-spans").innerHTML = `<table>
    <thead><tr><th>size</th><th>x0</th><th>wrap</th><th>text</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
  // If the correction panel is already open (e.g. the user is flipping
  // through pages with it pinned open), keep it in sync with the new page
  // instead of leaving yesterday's page's draft on screen.
  if ($("#correction-details").open) await loadCorrectionFields();
}

/** Populates the correction editor for whichever handbook/page is currently
 * selected in Page Explorer -- from an existing authored entry if one
 * exists, otherwise the raw extracted text as a starting draft. Available
 * for any page, not just ones the review queue flagged: the queue's
 * heuristics catch tables and diagrams, not every kind of bad extraction
 * (e.g. a two-column layout read in the wrong order). */
// Populated by loadCorrectionFields(), read by useCheckedAsStartingPoint()
// so the latter doesn't have to re-fetch chunks just to look up bodies.
let currentPageCandidates = [];

async function loadCorrectionFields() {
  const doc = $("#pages-doc-select").value;
  const n = $("#pages-page-select").value;
  if (!doc || !n) return;
  const page = Number(n);
  const details = $("#correction-details");
  const statusEl = $("#correction-status");
  statusEl.textContent = ""; statusEl.className = "status-msg";

  const [docs, existingTables, allChunks] = await Promise.all([
    fetchJSON("/api/docs"),
    fetchJSON("/api/authored-tables").catch(() => []),
    fetchJSON(`/api/chunks/${doc}`).catch(() => []),
  ]);
  const d = docs.find((x) => x.id === doc);
  if (!d) return;
  details.dataset.doc = doc;
  details.dataset.docId = d.doc_id;
  details.dataset.page = String(page);

  // A correction that covers several pages (a table that runs on, say)
  // should show up and be editable from ANY of those pages, not just the
  // one it happened to be started from -- otherwise a page-agnostic user
  // landing on, e.g., page 27 of a page-25-anchored correction would have
  // no idea one already exists, and could easily save a second, competing
  // one. So look for coverage first, and only fall back to a fresh
  // page-anchored id when nothing already covers this page.
  const existing = existingTables.find((t) => t.doc_id === d.doc_id && (t.covers_pages || []).includes(page));
  const authoredId = existing ? existing.authored_id : `page_${page}`;
  details.dataset.authoredId = authoredId;
  // Deliberately NOT pre-filled with the whole page's raw text for a new
  // correction: that's what led to accidentally re-including content (like
  // Vision/Core Values/Motto) that was already fine on its own. Check the
  // broken pieces below and use them as a starting point instead.
  $("#correction-title").value = existing ? existing.title : `Page ${page} (manual correction)`;
  $("#correction-breadcrumb").value = existing ? existing.breadcrumb : `[${d.source}]`;
  $("#correction-text").value = existing ? existing.text : "";
  resetCorrectionSearch();
  $("#correction-verified").checked = existing ? !!existing.verified : false;
  $("#correction-authored-by").value = existing ? (existing.authored_by || "") : rememberedAuthor();

  const rangeNote = $("#correction-page-range-note");
  const coveredPages = existing ? (existing.covers_pages || []) : [];
  rangeNote.textContent = coveredPages.length > 1
    ? `This correction covers pages ${Math.min(...coveredPages)}–${Math.max(...coveredPages)} -- editing here updates it for all of them.`
    : "";

  const alreadyChosen = new Set(existing ? (existing.supersedes || []) : []);
  currentPageCandidates = allChunks.filter(
    (c) => c.source_type === "pdf_extraction" && (c.pages || []).includes(page)
  );
  const listEl = $("#supersede-list");
  listEl.innerHTML = currentPageCandidates.length
    ? currentPageCandidates.map((c) => {
        const preview = c.chunk_text.length > 140 ? c.chunk_text.slice(0, 140) + "…" : c.chunk_text;
        return `
          <label class="supersede-row">
            <input type="checkbox" class="supersede-check" value="${esc(c.chunk_id)}"
              ${alreadyChosen.has(c.chunk_id) ? "checked" : ""}>
            <span>
              <strong>${esc(c.title)}</strong> <span class="hint">(${c.token_count} tok)</span>
              <span class="supersede-preview">${esc(preview)}</span>
            </span>
          </label>`;
      }).join("")
    : '<p class="hint" style="margin:0;">No other extracted content on this page.</p>';
}

/** Strips a chunk's breadcrumb prefix off its chunk_text, leaving just the
 * body -- chunker.py builds chunk_text as breadcrumb + "\n" + body, and
 * pdf_extraction chunks don't carry the body separately the way authored
 * entries do. */
function chunkBody(c) {
  const prefix = c.breadcrumb + "\n";
  return c.chunk_text.startsWith(prefix) ? c.chunk_text.slice(prefix.length) : c.chunk_text;
}

function useCheckedAsStartingPoint() {
  const checkedIds = new Set($$("#supersede-list .supersede-check:checked").map((cb) => cb.value));
  const chosen = currentPageCandidates.filter((c) => checkedIds.has(c.chunk_id));
  if (!chosen.length) {
    alert("Check at least one piece above first.");
    return;
  }
  $("#correction-text").value = chosen.map(chunkBody).join("\n\n");
  resetCorrectionSearch();
  if (chosen.length === 1) {
    $("#correction-title").value = chosen[0].title;
    $("#correction-breadcrumb").value = chosen[0].breadcrumb;
  }
}

async function saveCorrection() {
  const details = $("#correction-details");
  const docId = details.dataset.docId;
  const page = Number(details.dataset.page);
  const statusEl = $("#correction-status");
  if (!docId || !page) return;
  // Whatever loadCorrectionFields() resolved this to -- either an existing
  // entry's own id (if one already covers this page) or a fresh page_N id.
  // Not recomputed from the page here, so saving from page 27 updates the
  // same entry that was started on page 25 instead of forking a new one.
  const authoredId = details.dataset.authoredId || `page_${page}`;

  const text = $("#correction-text").value;
  if (!text.trim()) {
    statusEl.textContent = "Type in the correct text first.";
    statusEl.className = "status-msg bad";
    return;
  }

  const authoredBy = $("#correction-authored-by").value.trim();
  const supersedes = $$("#supersede-list .supersede-check:checked").map((cb) => cb.value);
  // A nudge, not a block: if this page has other extracted content but none
  // of it got checked, the original text for it will still ship alongside
  // whatever's being saved here. Sometimes that's correct (a table split
  // across pages, already superseded from a different one of them) -- so
  // this only asks, it doesn't refuse. Scoped to exactly what's being saved
  // right now, rather than trying to infer intent for content elsewhere in
  // the corpus after the fact (that approach was tried and produced false
  // positives on perfectly fine, deliberately-untouched content).
  if (currentPageCandidates.length && !supersedes.length) {
    const proceed = confirm(
      "None of the \"which extracted pieces are wrong\" boxes above are checked, so the original extracted " +
      "text for this page will still show up in the results alongside what you're saving here.\n\n" +
      "That's fine if it's already handled by a correction on another page (e.g. a table that continues there) " +
      "-- otherwise, go back and check the piece(s) this is meant to replace.\n\n" +
      "Save anyway?"
    );
    if (!proceed) return;
  }
  // A checked piece can itself span several pages (a table that runs on for
  // pages, say) -- cover all of them, not just the page this editor happens
  // to be anchored on, so the Review tab and gate checks recognize the fix
  // on every page it actually touches. EXCEPT any page some other, separate
  // correction already claims: without this, re-saving a page_25 entry
  // whose checked chunk spans pages 25-29 would silently annex page 26 away
  // from an intentionally separate page_26 entry on every save. Per-page
  // splitting (one correction per page, all sharing the same checked box)
  // is a legitimate way to work -- this just keeps it from self-colliding.
  const existingBeforeSave = await fetchJSON("/api/authored-tables").catch(() => []);
  const claimedByOthers = new Set(
    existingBeforeSave
      .filter((t) => t.doc_id === docId && t.authored_id !== authoredId)
      .flatMap((t) => t.covers_pages || [])
  );
  const supersededChunks = currentPageCandidates.filter((c) => supersedes.includes(c.chunk_id));
  const coveredPages = [...new Set([page, ...supersededChunks.flatMap((c) => c.pages || [])])]
    .filter((p) => p === page || !claimedByOthers.has(p))
    .sort((a, b) => a - b);
  const entry = {
    doc_id: docId,
    authored_id: authoredId,
    title: $("#correction-title").value.trim(),
    breadcrumb: $("#correction-breadcrumb").value.trim(),
    covers_pages: coveredPages,
    pages: coveredPages,
    page_labels: coveredPages,
    verified: $("#correction-verified").checked,
    authored_by: authoredBy,
    authored_on: new Date().toISOString().slice(0, 10),
    provenance: coveredPages.length > 1
      ? `Manually corrected via Page Explorer for pages ${coveredPages.join(", ")}.`
      : `Manually corrected via Page Explorer for page ${page}.`,
    text,
    supersedes,
  };
  try { localStorage.setItem("askwhiz_authored_by", authoredBy); } catch { /* ignore */ }

  const saveBtn = $("#correction-save");
  setBtnBusy(saveBtn, true, "Saving...");
  statusEl.textContent = ""; statusEl.className = "status-msg";
  try {
    const existing = await fetchJSON("/api/authored-tables");
    const idx = existing.findIndex((t) => t.doc_id === docId && t.authored_id === authoredId);
    // Merge onto a copy of the existing entry rather than replacing it
    // outright, so any field this form doesn't expose (verified_by, a
    // future supersedes list, anything else) survives re-saving.
    const merged = { ...(idx >= 0 ? existing[idx] : {}), ...entry };
    const updated = idx >= 0
      ? existing.map((t, i) => (i === idx ? merged : t))
      : [...existing, merged];
    await fetchJSON("/api/authored-tables", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables: updated }),
    });
    statusEl.textContent = "Saved. Re-run this handbook on the Review tab to apply it to the corpus.";
    statusEl.className = "status-msg ok";
  } catch (e) {
    statusEl.textContent = "Failed: " + e.message;
    statusEl.className = "status-msg bad";
  } finally {
    setBtnBusy(saveBtn, false);
  }
}

// ==================================================== SEARCH INDEX tab ====
// Steps 7-8 (embed + index) and Gate 4 (gold questions) behind one button.
// Building never touches the chatbot: a passing version still has to be
// copied into the chatbot project by a person.

let indexOverview = null;
let indexPollTimer = null;
const indexOpenDetails = new Set();

$("#index-build-btn").addEventListener("click", startIndexBuild);

async function loadIndexTab() {
  if (!indexOverview) {
    $("#index-handbooks").innerHTML = loadingBlock("Loading handbooks...");
    $("#index-versions").innerHTML = loadingBlock("Loading index versions...");
  }
  try {
    indexOverview = await fetchJSON("/api/index");
  } catch (e) {
    indexOverview = null;
    $("#index-handbooks").innerHTML = `<div class="gate-line bad">Couldn't load the search index page (${esc(e.message)}).
      If you just updated the app, the old version is probably still running: stop it with Ctrl+C in its
      terminal, run "python app.py" again, then refresh this page.</div>`;
    $("#index-versions").innerHTML = "";
    $("#index-build-btn").disabled = true;
    return;
  }
  renderIndexHandbooks();
  renderIndexVersions();
  renderIndexJob(indexOverview.job);
  if (indexOverview.job.state === "running") pollIndexJob();
}

function selectedIndexDocs() {
  return $$("#index-handbooks input[type=checkbox]:checked").map((c) => c.value);
}

function renderIndexHandbooks() {
  const o = indexOverview;
  const wrap = $("#index-handbooks");
  const previous = new Set(selectedIndexDocs());
  const keepPrevious = previous.size > 0;
  wrap.innerHTML = "";
  for (const h of o.handbooks) {
    const row = document.createElement("label");
    row.className = "index-handbook" + (h.indexable ? "" : " disabled");
    const checked = h.indexable && (keepPrevious ? previous.has(h.id) : true);
    row.innerHTML = `
      <input type="checkbox" value="${esc(h.id)}" ${checked ? "checked" : ""} ${h.indexable ? "" : "disabled"}>
      <div>
        <div><strong>${esc(h.source)}</strong></div>
        <div class="meta">${h.indexable
          ? `Audience: ${esc(h.audience)} · ${h.chunk_count} sections · ${o.gold_by_audience[h.audience] || 0} gold questions`
          : `Can't be included: ${esc(h.reason)}.`}</div>
      </div>`;
    row.querySelector("input").addEventListener("change", updateIndexWarnings);
    wrap.appendChild(row);
  }
  const warn = document.createElement("div");
  warn.id = "index-warnings";
  wrap.appendChild(warn);
  $("#index-build-status").textContent = `Will be saved as ${o.next_name}`;
  $("#index-build-status").className = "status-msg";
  updateIndexWarnings();
}

function updateIndexWarnings() {
  const o = indexOverview;
  const chosen = o.handbooks.filter((h) => selectedIndexDocs().includes(h.id));
  const warnings = [];
  const byAudience = {};
  for (const h of chosen) (byAudience[h.audience] ||= []).push(h.source);
  for (const [aud, names] of Object.entries(byAudience)) {
    if (names.length > 1) {
      warnings.push(`${names.join(" and ")} have the same audience (${aud}). ` +
        `If one is a newer edition of the other, include only the newer one, or the chatbot will see both and may quote the old rules.`);
    }
  }
  for (const [aud, n] of Object.entries(o.gold_by_audience)) {
    if (!byAudience[aud]) {
      warnings.push(`No handbook for audience "${aud}" is selected, so its ${n} gold question${n > 1 ? "s" : ""} will find nothing and Gate 4 will likely fail.`);
    }
  }
  for (const aud of Object.keys(byAudience)) {
    if (!o.gold_by_audience[aud]) {
      warnings.push(`There are no gold questions for audience "${aud}", so Gate 4 can't tell whether search works for it. Add some to gold_expected.json.`);
    }
  }
  $("#index-warnings").innerHTML = warnings.map((w) => `<div class="index-warn">${esc(w)}</div>`).join("");
  $("#index-build-btn").disabled = chosen.length === 0 || (o.job && o.job.state === "running");
}

async function startIndexBuild() {
  const docs = selectedIndexDocs();
  if (!docs.length) {
    $("#index-build-status").textContent = indexOverview
      ? "Tick at least one handbook first."
      : "The handbook list didn't load -- see the message above.";
    $("#index-build-status").className = "status-msg bad";
    return;
  }
  const btn = $("#index-build-btn");
  setBtnBusy(btn, true, "Starting...");
  try {
    await fetchJSON("/api/index/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ docs }),
    });
    pollIndexJob();
  } catch (e) {
    setBtnBusy(btn, false);
    $("#index-build-status").textContent = "Couldn't start: " + e.message;
    $("#index-build-status").className = "status-msg bad";
  }
}

function pollIndexJob() {
  clearTimeout(indexPollTimer);
  const btn = $("#index-build-btn");
  setBtnBusy(btn, true, "Building...");
  const tick = async () => {
    let job;
    try { job = await fetchJSON("/api/index/job"); } catch { indexPollTimer = setTimeout(tick, 3000); return; }
    renderIndexJob(job);
    if (job.state === "running") {
      indexPollTimer = setTimeout(tick, 1500);
    } else {
      setBtnBusy(btn, false);
      if (job.state === "done") indexOpenDetails.add(job.name);
      await loadIndexTab();
    }
  };
  tick();
}

function renderIndexJob(job) {
  const wrap = $("#index-job");
  if (!job || job.state === "idle") { wrap.innerHTML = ""; return; }
  let head;
  if (job.state === "running") {
    head = `<div class="loading-block"><span class="spinner"></span>${esc(job.name)}: ${esc(job.step || "Working")}... ` +
      `The first build downloads the search model (about 130 MB), so it can take a few minutes.</div>`;
  } else if (job.state === "failed") {
    head = `<div class="gate-line bad">Build of ${esc(job.name)} failed: ${esc(job.error)}</div>`;
  } else {
    const r = job.result;
    head = r.gate4.ok
      ? `<div class="gate-line ok">${esc(job.name)} is built and passed Gate 4. Details are in the table below.</div>`
      : `<div class="gate-line bad">${esc(job.name)} is built but FAILED Gate 4: ${esc(r.gate4.problems.join("; "))}. Don't use this one.</div>`;
  }
  const log = job.log
    ? `<details class="stage-log${job.state === "failed" ? " fail" : ""}" ${job.state !== "done" ? "open" : ""}>
         <summary>Technical log</summary><pre style="margin:0; white-space:pre-wrap;">${esc(job.log)}</pre></details>`
    : "";
  wrap.innerHTML = head + log;
  const pre = wrap.querySelector("pre");
  if (pre && job.state === "running") pre.parentElement.scrollTop = pre.parentElement.scrollHeight;
}

function rankPill(rank) {
  if (rank === 1) return `<span class="rank-pill rank-1">1st</span>`;
  if (rank == null) return `<span class="rank-pill rank-miss">missed</span>`;
  return `<span class="rank-pill rank-mid">#${rank}</span>`;
}

function renderIndexVersions() {
  const versions = indexOverview.versions;
  const wrap = $("#index-versions");
  if (!versions.length) {
    wrap.innerHTML = `<p class="hint">No index built yet.</p>`;
    return;
  }
  const rows = versions.map((v, i) => {
    const older = versions.slice(i + 1).find((x) => x.summary);
    let delta = "";
    if (v.summary && older) {
      const d = v.summary.mrr - older.summary.mrr;
      if (Math.abs(d) >= 0.0005) delta = ` <span class="${d > 0 ? "delta-up" : "delta-down"}">${d > 0 ? "+" : ""}${d.toFixed(3)}</span>`;
    }
    const s = v.summary;
    const n = s ? s.n : 0;
    const gate = v.gate4 == null ? `<span class="badge warn">not tested</span>`
      : v.gate4.ok ? `<span class="badge ok">passed</span>` : `<span class="badge bad">failed</span>`;
    const built = v.built_at ? v.built_at.replace("T", " ").slice(0, 16) : "";
    return `
      <tr data-name="${esc(v.name)}">
        <td><strong>${esc(v.name)}</strong></td>
        <td style="white-space:nowrap;">${esc(built)}</td>
        <td>${v.handbooks.map(esc).join("<br>")}</td>
        <td class="num">${v.passage_count ?? ""}</td>
        <td class="num">${s ? `${s["hit@1"]}/${n}` : "–"}</td>
        <td class="num">${s ? `${s["hit@3"]}/${n}` : "–"}</td>
        <td class="num">${s ? `${s["hit@5"]}/${n}` : "–"}</td>
        <td class="num">${s ? s.mrr.toFixed(3) : "–"}${delta}</td>
        <td>${gate}</td>
        <td style="white-space:nowrap;">
          <button type="button" class="small-btn details-btn">${indexOpenDetails.has(v.name) ? "Hide" : "Details"}</button>
          <a class="link-btn small-btn" href="/api/index/${encodeURIComponent(v.name)}/download">Download</a>
        </td>
      </tr>
      ${indexOpenDetails.has(v.name) ? `<tr class="detail-row"><td colspan="10">${indexDetails(v)}</td></tr>` : ""}`;
  }).join("");
  wrap.innerHTML = `
    <div class="index-table-wrap"><table class="index-table">
      <thead><tr><th>Version</th><th>Built</th><th>Handbooks</th><th class="num">Passages</th>
        <th class="num">1st</th><th class="num">Top 3</th><th class="num">Top 5</th><th class="num">MRR</th>
        <th>Gate 4</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  $$(".details-btn", wrap).forEach((b) => b.addEventListener("click", () => {
    const name = b.closest("tr").dataset.name;
    indexOpenDetails.has(name) ? indexOpenDetails.delete(name) : indexOpenDetails.add(name);
    renderIndexVersions();
  }));
}

function indexDetails(v) {
  const rule = indexOverview.gate4_rule;
  let html = "";
  if (v.gate4 && !v.gate4.ok) {
    html += `<div class="gate-line bad">Gate 4 failed: ${esc(v.gate4.problems.join("; "))}. Don't use this version.</div>`;
  } else if (v.gate4 && v.gate4.ok) {
    html += `<div class="gate-line ok">Passed Gate 4: at least ${rule.min_hit5} gold questions in the top 5, and
      "${esc(rule.must_be_first.join('", "'))}" answered first.</div>
      <ol class="publish-steps">
        <li>Download this version (or copy the <code>${esc(v.name)}</code> folder).</li>
        <li>Put it in the chatbot project and change the chatbot's index setting to <code>${esc(v.name)}</code>.</li>
        <li>Commit and push. Render redeploys, and keeps the old version running if the deploy fails.</li>
      </ol>`;
  }
  if (v.results) {
    html += `<table class="index-table" style="margin-top:0.6rem;">
      <thead><tr><th>Gold question</th><th>Right answer</th><th>Best-matching passage</th></tr></thead><tbody>` +
      v.results.map((r) => `<tr><td>${esc(r.q)}${r.audience ? ` <span class="badge warn">${esc(r.audience)}</span>` : ""}</td><td>${rankPill(r.rank)}</td>
        <td class="hint" style="margin:0;">${esc(r.top_match || "")}</td></tr>`).join("") +
      `</tbody></table>`;
  } else {
    html += `<p class="hint">This version has no gold results saved.</p>`;
  }
  return html;
}

// ============================================================ init ====

loadDocs();
