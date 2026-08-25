# AskWhiz Handbook Extractor

Turns a student handbook PDF into a clean, chunked text corpus, ready to be embedded for
[AskWhiz](#status--whats-next). It's a two-part project:

- A **pipeline** (`extract.py` → `chunker.py`, orchestrated by `ingest.py`) that pulls text out of a
  PDF, splits it into headed sections, and freezes the result as JSONL.
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

## How it works

1. **Add a handbook** — upload any handbook PDF and give it a name. It's extracted and chunked
   automatically with generic settings (no tuning required to get a first pass).
2. **Review** — pages the pipeline is unsure about get flagged automatically:
   - the text promises a table ("the table below shows...") but none was detected,
   - a page has no extractable text but does have an image (likely scanned),
   - a page is dense with vector graphics and no detected table (likely a diagram/flowchart, whose
     text usually gets extracted in a visually-scrambled reading order).

   Each flagged page shows the actual PDF page image next to its raw extracted text, so you can
   either mark it "looks fine" or type in the correct transcription right there, side by side.
3. **Settings** (Advanced) — per-handbook tuning: pages to skip (cover/TOC), known typo fixes, and
   how headings are detected (font size/weight, left-margin position). Existing handbooks already
   have this tuned; new ones work fine without it and lean on the review queue instead.
4. **Browse Results / Page Explorer** (Advanced) — inspect the frozen chunks, or a single page's
   extracted text and font/position spans, for debugging.

## The gates

Ported from the original CLI pipeline (`ingest.py`) and extended in the app:

| Gate | Checks | Refuses to freeze if... |
|---|---|---|
| 1 — drift | PDF page count vs. what `pipeline_config.json` expects | a new edition doesn't match the config it was tuned against |
| 2 — authored tables | Required hand-transcribed tables are present *and verified* | a required correction is missing or unverified |
| 3 — review queue | Every auto-flagged page has been resolved | a flagged page is still pending, or a saved correction hasn't been re-processed into the corpus yet |

All three are re-checked live from the actual frozen corpus, not from in-progress edits — so the app
can't report a clean pass while a saved-but-not-yet-applied correction sits stale.

## Project layout

```
extract.py              PDF -> per-page text + font/position spans (pdfplumber)
chunker.py               spans -> headed sections -> chunks.jsonl, merges in corrections
normalize.py             historical migration script; not part of the live pipeline output
ingest.py                CLI orchestrator: extract -> normalize -> chunk, gates 1 & 2
verify_extraction.py     standalone pre-flight PDF sanity checker (CLI)
app.py                   FastAPI web app: runs the pipeline, gate 3, the review/correction UI
static/                  frontend (vanilla HTML/CSS/JS, no build step)

pipeline_config.json     per-handbook settings, editable from the app's Settings tab
authored_tables.json     hand-transcribed corrections for content the parser can't read
review_state.json        pages a human has explicitly dismissed as "fine as-is"

uploads/                 source PDFs for handbooks added through the app
chunks/                  the frozen, committed corpus -- one .jsonl per handbook
raw/, clean/             intermediate extraction output, regenerated on each run (not committed)
```

## Status / what's next

The corpus is built and frozen here; this repo does not embed it or serve a chatbot. The documented
next step (see `embed_stub()` in `ingest.py`) is: embed each chunk (Titan Embed Text v2 via AWS
Bedrock), store the vectors in an index (AWS S3 Vectors, with `audience` as a filterable field), and
build the retrieval + generation layer on top — none of which is implemented yet.
