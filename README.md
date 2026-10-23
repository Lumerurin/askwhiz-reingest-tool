# AskWhiz Handbook Extractor

Builds AskWhiz's knowledge base from two sources and turns it into the search index the chatbot
reads:

- **Student handbook PDFs** (College, Senior High, High School), extracted, reviewed by a person,
  and frozen as chunks.
- **MMCM's BoldDesk help center** (support.mcm.edu.ph), synced automatically. BoldDesk is the main,
  living knowledge base: when MMCM publishes or edits an article there, a sync picks it up.

It's a two-part project:

- A **pipeline** (`extract.py` → `chunker.py`, orchestrated by `ingest.py`) that pulls text out of a
  PDF, splits it into headed sections, and freezes the result as JSONL. `bolddesk_sync.py` does the
  same for help-center articles, and `build_index.py` turns all chunks into a search index.
- A **local web app** (`app.py` + `static/`) wrapped around that pipeline, so a non-technical person
  can drop in a PDF, watch it get processed, and fix the handful of things a PDF parser can't read on
  its own (tables rendered as pasted images, flowcharts/diagrams, scanned pages) — without touching
  the terminal.

## Running it

```
pip install -r requirements.txt
python app.py
```

Then open `http://127.0.0.1:8000`.

To sync the BoldDesk help center, create a file named `.env` in this folder first. It is listed in
`.gitignore`, so it never goes to GitHub. Never paste the key anywhere else.

```
BOLDDESK_DOMAIN=support.mcm.edu.ph
BOLDDESK_API_KEY=the key MMCM provided
```

The key should come from a BoldDesk account whose role can only view the Knowledge Base. A BoldDesk
key carries all the permissions of the account that created it, so a key from a normal staff
account could also read student tickets.

## Using the app

The app has four main tabs (**Handbooks**, **Review**, **Page Explorer**, **Search Index**) for
everyday work, and three **Advanced** ones (**Settings**, **Manual Corrections**, **Browse Results**)
for tuning and inspection.

### 1. Handbooks — add one, see its status

Give it a name (and optionally who it's for), pick a PDF, and click **Add & process**. It's
extracted and chunked automatically with generic settings — no tuning needed to get a first pass,
even for a handbook the pipeline has never seen before. Each card shows whether it's been processed
and how many pages still need a look; click a card to jump straight into reviewing it.

Removing a handbook also removes any corrections you'd authored for it.

### 2. Review — everything that needs a human look, one page-sorted list

Pick a handbook to see every page worth attention, in page order. Two kinds of pages land here:

- **Auto-flagged pages** — the pipeline noticed something on its own: the text promises a table
  ("the table below shows...") but none was detected, a page has no extractable text but does have
  an image (likely scanned), or a page is dense with vector graphics and no detected table (likely a
  diagram/flowchart, whose text usually comes out in a scrambled reading order).
- **Manually-corrected pages** — anything you've typed a correction for in Page Explorer, even if
  the automatic scan never flagged it. This is important: the scan's heuristics catch tables and
  diagrams, but not everything — a two-column layout read in the wrong order, for instance, won't
  trip any of them, so being able to fix *any* page and have it show up here matters.

Each page shows a status and the buttons that make sense for it:

| Status | Meaning | What you can do |
|---|---|---|
| **pending** | Auto-flagged, nothing done yet | **Looks fine** (dismiss it, optionally with a note) or **Fix this page** (opens the correction editor in Page Explorer) |
| **covered** | A verified correction is saved *and already baked into the corpus* | **Unverify correction**, if you need to redo it |
| **needs_rerun** | A verified correction is saved, but the pipeline hasn't been re-run since — so it's not live yet | **Edit the correction**, or just go re-run the pipeline (see below) |
| **unverified** | A draft correction exists but hasn't been checked against the real page image yet | **Finish & verify** |
| **cleared** | You marked this page fine as-is | shown greyed out, with your note if you left one |

A saved correction never silently "just works" — it only counts once the pipeline has actually been
re-run with it in place. Click **Process** at the top of the Review tab to re-run the pipeline for
the selected handbook; do this after saving any correction (or a batch of them) so `needs_rerun`
pages go live.

### 3. Page Explorer — look at any page, fix any page

Pick a handbook and a page. Besides the page dropdown, you can step through pages with the
**← Prev / Next →** buttons or the left/right arrow keys (these don't interfere with typing in any
text field). A "page X of N" indicator shows where you are.

The page image sits next to its extracted text, with a **search box** above the text so you can
find a word or phrase on the current page — matches are highlighted, Enter/Shift+Enter or the ↑/↓
buttons cycle between them, and your search term carries over as you flip pages.

**Correcting a page's text** works on *any* page, not just ones the Review queue flagged:

1. Open **Correct this page's text**. If the page has other separately-extracted pieces of content
   on it, they're listed as checkboxes — check whichever ones are actually wrong; leave anything
   that's already fine unchecked, and it stays untouched.
2. Click **Use checked pieces as a starting point** to pull just those checked pieces' text into the
   box below, as a draft to clean up. (This is deliberate: pre-filling the *whole* page's text was
   tried and led to accidentally re-touching content that was already fine.)
3. Edit the **Text** field — it has its own find box above it, useful for jumping around in a long
   correction (it moves your cursor/selection to each match rather than highlighting, since a plain
   text box can't do inline highlighting).
4. Fill in **Title**, **Breadcrumb**, and **Authored by** (remembered across sessions), check
   **I checked this against the page image above** once you actually have, and **Save**.

A few things worth knowing:

- **If a correction spans several pages** (a table that runs on for pages, say), just check the
  piece that spans them — the correction will cover every page that piece touches, and opening the
  correction editor from *any* of those pages will find and let you edit that same entry, instead of
  only recognizing it on the page you first saved it from.
- **If you save without checking any of the boxes** on a page that does have other extracted content,
  you'll get a warning that the original extracted text will still show up in the results alongside
  what you're saving. That's expected and fine if it's already handled by a correction on another
  page (e.g. a multi-page table you're splitting one page at a time) — otherwise, go back and check
  the piece it's meant to replace.
- Saving doesn't apply anything to the corpus by itself — go back to **Review** and click **Process**
  to re-run the pipeline and bake it in.

### 4. Search Index — sync BoldDesk, build the index, check it

This is where the knowledge base becomes something the chatbot can search.

1. **Sync from BoldDesk** (on the Help Center row). Downloads articles that are new or changed since
   the last sync and drops ones MMCM unpublished. Only articles that are **Published, Active and
   visible to Any Users** are ever downloaded: drafts, staff-only and group-only articles never are.
   The report under the button lists what was new, updated or removed.
2. **Tick the sources** to include. Each handbook has its own audience (`college`, `shs`,
   `high-school-handbook`), so its rules only answer that group's questions. Help-center articles
   have audience `all`: they are searched for every group. The tab warns you if two handbooks share
   an audience (an old and a new edition) or a group has no gold questions.
3. **Build search index.** Saves a new numbered folder (`index_v1`, `index_v2`, ...) and runs the gold
   questions (Gate 4). Earlier versions are never overwritten.
4. **Index versions** lists every build with its scores and a passed/failed badge. **Details** shows
   how each gold question ranked; **Download** gives the folder as a zip.

Building an index **does not change the chatbot**. A passing version is copied into the chatbot
project by a person. (Once the chatbot server is running, it will sync BoldDesk on its own every few
minutes; this tab is for building and checking by hand.)

Command-line equivalents: `python bolddesk_sync.py` (add `--full` to re-download everything),
`python build_index.py --chunks chunks\*.jsonl --out index_vN`, `python eval_gold.py --index index_vN`.

### 5. Settings (Advanced)

Per-handbook fine-tuning: which pages to skip (cover, table of contents), running headers to strip,
known typos to fix, and how headings are detected (font weight/size, left-margin position). New
handbooks work fine with none of this set — these are refinements you reach for if the review queue
keeps flagging the same kind of thing, not requirements for a first pass.

### 6. Manual Corrections (Advanced)

A flat list of every authored correction across every handbook, editable in bulk. Most day-to-day
correction work happens through Review + Page Explorer now, since that surfaces every correction
(finished or still a draft) in one page-sorted list right next to the page image — this tab is more
useful for a bird's-eye view across a whole handbook, or bulk edits.

### 7. Browse Results (Advanced)

Inspect the frozen chunks for a handbook — search by text, or download the raw `.jsonl`.

## The gates

Ported from the original CLI pipeline (`ingest.py`) and extended in the app:

| Gate | Checks | Refuses to freeze if... |
|---|---|---|
| 1 — drift | PDF page count vs. what `pipeline_config.json` expects | a new edition doesn't match the config it was tuned against |
| 2 — authored tables | Required hand-transcribed tables are present *and verified* | a required correction is missing or unverified |
| 3 — review queue | Every flagged or manually-corrected page is resolved | a page is still `pending`, `unverified`, or a saved correction hasn't been re-processed (`needs_rerun`) |
| 4 — gold questions | A newly built search index finds the right section for the questions in `gold_expected.json` (33: College, SHS, High School and Help Center) | fewer than 90% are in the top 5 (30 of 33), or "What is the school motto?" isn't answered first for every audience it's asked for |

Gates 1–3 are re-checked live from the actual frozen corpus, not from in-progress edits — so the app
can't report a clean pass while a saved-but-not-yet-applied correction sits stale. Gate 4 runs on the
Search Index tab (or `ingest.py --embed`) every time an index is built.

## Project layout

```
extract.py              PDF -> per-page text + font/position spans (pdfplumber)
chunker.py               spans -> headed sections -> chunks.jsonl, merges in corrections
normalize.py             historical migration script; not part of the live pipeline output
ingest.py                CLI orchestrator: extract -> normalize -> chunk, gates 1 & 2 (+ index, gate 4 with --embed)
passages.py              splits a chunk into small labelled search passages
build_index.py           chunks -> search index folder (passages + vectors), local model, no cloud
retrieve.py              hybrid keyword + meaning search, used by the chatbot and the tests
eval_gold.py             scores an index on gold_expected.json (gate 4 uses this)
gold_expected.json       the gold questions and which chunks answer them
bolddesk_probe.py        read-only first look: lists help-center articles and their visibility
bolddesk_sync.py         read-only sync: published public articles -> chunks/bolddesk_kb.chunks.jsonl
verify_extraction.py     standalone pre-flight PDF sanity checker (CLI)
app.py                   FastAPI web app: runs the pipeline, gate 3, the review/correction UI,
                         and the Search Index tab (build + gate 4)
static/                  frontend (vanilla HTML/CSS/JS, no build step)

pipeline_config.json     per-handbook settings, editable from the app's Settings tab
authored_tables.json     hand-transcribed corrections for content the parser can't read
review_state.json        pages a human has explicitly dismissed as "fine as-is"

uploads/                 source PDFs for handbooks added through the app
chunks/                  the committed corpus -- one .jsonl per handbook, plus bolddesk_kb.chunks.jsonl
                         (the help-center articles as of the last sync)
bolddesk_raw/            cached article downloads, so a sync only fetches what changed (not committed)
bolddesk_inventory.json  output of bolddesk_probe.py (not committed)
.env                     BoldDesk domain + API key (never committed)
raw/, clean/             intermediate extraction output, regenerated on each run (not committed)
index/, index_v*/        search index builds, made on the Search Index tab (not committed here; a
                         passing version is copied into the chatbot repo)
```

## Status / what's next

The knowledge base is built: three handbooks plus the BoldDesk help center (32 published articles,
41 chunks). `index_v2` (October 23, 2026) passed Gate 4 with all 33 gold questions in the top 5 and
an MRR of 0.846. AWS was dropped in October 2026: the index is a folder of files built with a small
local embedding model (bge-small-en-v1.5), with no cloud service and no per-use cost. See
`INDEX_README.md` for how search works and the score history.

Next to build: the answering engine (FastAPI). It will search the index, ask the language model
(GLM via the Z.ai API, or a model on MMCM's own server -- not decided yet), cite the handbook page or
help-center link, prefer the 2026 help center where it disagrees with the 2020 handbooks, log every
question, and re-sync BoldDesk automatically every ~15 minutes (switching to the new index only if
Gate 4 passes). Then the chat widget for the school website.
