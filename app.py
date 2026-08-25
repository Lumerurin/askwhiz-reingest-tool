#!/usr/bin/env python3
"""
app.py — AskWhiz v2 handbook extractor, local web app.

A FastAPI wrapper around extract.py / normalize.py / chunker.py / ingest.py so
the pipeline can be run, reviewed, and configured from a browser instead of
the terminal. It shells out to the same scripts ingest.py uses and imports
ingest.py's gate functions directly -- no extraction, chunking, or gate rule
is reimplemented here. pipeline_config.json (used by normalize.py, chunker.py
and ingest.py) is the single editable source of per-handbook settings; this
app reads and writes that file instead of touching CONFIG dicts in code.

    pip install -r requirements.txt
    python app.py
    -> open http://127.0.0.1:8000
"""

import contextlib
import datetime
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import extract
import ingest

HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = HERE / "pipeline_config.json"
AUTHORED_PATH = HERE / "authored_tables.json"
REVIEW_STATE_PATH = HERE / "review_state.json"
RAW_DIR = HERE / "raw"
CLEAN_DIR = HERE / "clean"
CHUNKS_DIR = HERE / "chunks"
UPLOADS_DIR = HERE / "uploads"
STATIC_DIR = HERE / "static"

# A page with vector graphics (boxes/arrows) above this count, and no table
# pdfplumber could parse, is almost certainly a flowchart or diagram -- text
# extracted from it reads as scrambled fragments (boxes are read in a linear
# order that doesn't match how a human reads the shape). Calibrated against
# both existing handbooks: at 20, it catches the one real flowchart page
# (SHS p.21, 37 shapes) with zero false positives among ordinary pages with
# a letterhead graphic or a signature line (max 17 shapes).
DIAGRAM_SHAPE_THRESHOLD = 20

TABLE_PROMISE_RE = re.compile(
    r"table below|following table|table shows|shown in the table|as indicated in the table",
    re.IGNORECASE,
)

app = FastAPI(title="AskWhiz Extractor")


# --------------------------------------------------------------- helpers ---

def read_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_config(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    # ingest.py's gate functions read module-level EXPECTED / REQUIRED_AUTHORED
    # loaded once at import time -- refresh them so gates see the new config.
    ingest.EXPECTED, ingest.REQUIRED_AUTHORED = ingest.load_pipeline_config(CONFIG_PATH)


def validate_doc_config(doc_cfg: dict) -> None:
    required = ("doc_id", "source", "audience", "pdf_filename", "page_label_offset",
                "suppress_pages", "expected_pages_in", "normalize", "chunker")
    missing = [k for k in required if k not in doc_cfg]
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")

    norm, chunk = doc_cfg["normalize"], doc_cfg["chunker"]
    for k in ("strip_line_patterns", "typos"):
        if k not in norm:
            raise ValueError(f"normalize.{k} is required")
    for k in ("footer_pattern", "h1_x0", "missing_tables", "typos"):
        if k not in chunk:
            raise ValueError(f"chunker.{k} is required")

    for p in norm["strip_line_patterns"]:
        try:
            re.compile(p)
        except re.error as e:
            raise ValueError(f"normalize.strip_line_patterns: bad regex {p!r}: {e}")
    try:
        re.compile(chunk["footer_pattern"])
    except re.error as e:
        raise ValueError(f"chunker.footer_pattern: bad regex: {e}")

    h1 = chunk["h1_x0"]
    if h1 is not None and (not isinstance(h1, list) or len(h1) != 2):
        raise ValueError("chunker.h1_x0 must be null or a [min, max] pair")

    for stage, typos in (("normalize", norm["typos"]), ("chunker", chunk["typos"])):
        for page, fixes in typos.items():
            try:
                int(page)
            except ValueError:
                raise ValueError(f"{stage}.typos: page key {page!r} is not a number")
            for pair in fixes:
                if not (isinstance(pair, list) and len(pair) == 2):
                    raise ValueError(f"{stage}.typos[{page}]: each fix must be a [wrong, right] pair")

    if not isinstance(doc_cfg["suppress_pages"], list):
        raise ValueError("suppress_pages must be a list of page numbers")


def call_gate(fn, *args):
    """Run one of ingest.py's gate_* functions and capture its verdict instead
    of letting it exit the process. gates_on=True makes them raise SystemExit
    on failure -- that's the exact behavior we want to surface in the UI."""
    buf = io.StringIO()
    ok, message = True, None
    try:
        with contextlib.redirect_stdout(buf):
            fn(*args)
    except SystemExit as e:
        ok, message = False, str(e).strip()
    return {"ok": ok, "message": message, "log": buf.getvalue().strip()}


def run_stage(script: str, *args: str) -> dict:
    cmd = [sys.executable, str(HERE / script), *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
    return {
        "stage": script.removesuffix(".py"),
        "cmd": " ".join(cmd[1:]),
        "returncode": r.returncode,
        "ok": r.returncode == 0,
        "stdout": r.stdout,
        "stderr": r.stderr,
    }


def pdf_path_for(doc_cfg: dict) -> pathlib.Path:
    return HERE / doc_cfg["pdf_filename"]


def actual_page_count(pdf_path: pathlib.Path) -> Optional[int]:
    if not pdf_path.exists():
        return None
    try:
        return ingest.count_pdf_pages(pdf_path)
    except Exception:
        return None


def chunk_file_for(doc_cfg: dict) -> pathlib.Path:
    return CHUNKS_DIR / f"{doc_cfg['doc_id']}.chunks.jsonl"


def load_chunks(doc_cfg: dict) -> list:
    f = chunk_file_for(doc_cfg)
    if not f.exists():
        return []
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_authored() -> list:
    if not AUTHORED_PATH.exists():
        return []
    return json.loads(AUTHORED_PATH.read_text(encoding="utf-8"))


def read_review_state() -> dict:
    if not REVIEW_STATE_PATH.exists():
        return {}
    return json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8"))


def write_review_state(state: dict) -> None:
    tmp = REVIEW_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(REVIEW_STATE_PATH)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower()).strip("-")
    return s or "handbook"


def scan_doc_for_review(doc_cfg: dict) -> list:
    """Find pages likely to have extracted badly: a promised table that
    didn't parse, a page with no text but an image (scanned/raster page), or
    a page dense with vector shapes and no table (a flowchart/diagram --
    pdfplumber reads its boxes in a linear order that scrambles the text).
    Mirrors verify_extraction.py's checks, plus the diagram heuristic."""
    pdf_path = pdf_path_for(doc_cfg)
    if not pdf_path.exists():
        return []
    import pdfplumber
    suppressed = set(doc_cfg["suppress_pages"])
    flags = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if page.page_number in suppressed:
                continue
            text = page.extract_text() or ""
            tables = extract.real_tables(page)
            vec_shapes = len(page.rects) + len(page.lines) + len(page.curves)
            reasons = []
            if TABLE_PROMISE_RE.search(text) and not tables:
                reasons.append("table_promised_not_extracted")
            if not text.strip() and page.images:
                reasons.append("no_text_with_image")
            if vec_shapes > DIAGRAM_SHAPE_THRESHOLD and not tables:
                reasons.append("complex_diagram")
            if reasons:
                flags.append({"page": page.page_number, "reasons": reasons})
    return flags


def frozen_authored_chunks(doc_cfg: dict) -> dict:
    """The table_transcription chunks actually sitting in the frozen corpus
    right now, keyed by chunk_id -- as opposed to authored_tables.json, which
    is just a human's edits and isn't live until the pipeline re-runs."""
    return {c["chunk_id"]: c for c in load_chunks(doc_cfg) if c.get("source_type") == "table_transcription"}


def review_items_for(doc: str, doc_cfg: dict) -> list:
    """Live-scan a doc for risky pages and merge in what's already resolved:
    covered by a verified authored chunk, or explicitly cleared by a human.

    A verified authored_tables.json entry only counts as "covered" if it's
    actually present in the frozen chunks -- otherwise the review queue would
    show a page as fixed the instant someone hits Save, even though the real
    corpus (what a chunker.py run last produced) still has the old content
    until the pipeline is re-run. "needs_rerun" makes that gap visible
    instead of silently passing."""
    flags = scan_doc_for_review(doc_cfg)
    authored = [a for a in read_authored() if a.get("doc_id") == doc_cfg["doc_id"]]
    cleared = read_review_state().get(doc, {})
    frozen = frozen_authored_chunks(doc_cfg)

    out = []
    for f in flags:
        page = f["page"]
        verified_here = [a for a in authored if page in a.get("covers_pages", []) and a.get("verified")]
        covering, stale = [], False
        for a in verified_here:
            covering.append(a["authored_id"])
            cid = f"{doc_cfg['doc_id']}_authored_{a['authored_id']}"
            frozen_chunk = frozen.get(cid)
            expected_text = a["breadcrumb"] + "\n" + a["text"]
            if frozen_chunk is None or frozen_chunk.get("chunk_text") != expected_text:
                stale = True
        if covering and stale:
            status = "needs_rerun"
        elif covering:
            status = "covered"
        elif str(page) in cleared:
            status = "cleared"
        else:
            status = "pending"
        out.append({**f, "status": status, "covering_authored_ids": covering,
                     "cleared_note": cleared.get(str(page), {}).get("note")})
    return out


# ------------------------------------------------------------------ docs ---

@app.get("/api/docs")
def list_docs():
    cfg = read_config()
    out = []
    for doc, d in cfg["docs"].items():
        pdf_path = pdf_path_for(d)
        actual = actual_page_count(pdf_path)
        raw_pages = len(list((RAW_DIR / doc).glob("*.txt"))) if (RAW_DIR / doc).exists() else 0
        chunks = load_chunks(d)
        out.append({
            "id": doc,
            "doc_id": d["doc_id"],
            "source": d["source"],
            "audience": d["audience"],
            "pdf_filename": d["pdf_filename"],
            "pdf_exists": pdf_path.exists(),
            "actual_pages": actual,
            "expected_pages": d["expected_pages_in"],
            "pages_match": actual is not None and actual == d["expected_pages_in"],
            "raw_pages": raw_pages,
            "chunk_count": len(chunks),
            "short_chunk_count": sum(1 for c in chunks if c.get("short_section")),
        })
    return out


@app.post("/api/docs/{doc}/pdf")
async def upload_pdf(doc: str, file: UploadFile = File(...)):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    d = cfg["docs"][doc]

    body = await file.read()
    tmp = HERE / f".upload_{doc}.tmp.pdf"
    tmp.write_bytes(body)
    try:
        pages = ingest.count_pdf_pages(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"not a readable PDF: {e}")

    dest = pdf_path_for(d)
    tmp.replace(dest)
    return {"doc": doc, "pdf_filename": d["pdf_filename"], "actual_pages": pages,
            "expected_pages": d["expected_pages_in"], "pages_match": pages == d["expected_pages_in"]}


@app.post("/api/handbooks")
async def add_handbook(name: str = Form(...), audience: str = Form(""), file: UploadFile = File(...)):
    """Register a brand-new handbook (not just college/SHS) from an uploaded
    PDF. Settings start fully generic -- no suppressed pages, font-size-only
    heading detection, no typo fixes -- and expected_pages_in is set to
    whatever this PDF actually has, so Gate 1 only fires on a *future* edition
    that drifts from this one. Quality gaps get caught by the review queue
    (GET /api/docs/{doc}/review) rather than tuned upfront."""
    if not name.strip():
        raise HTTPException(400, "name is required")

    cfg = read_config()
    body = await file.read()
    tmp = HERE / ".new_handbook.tmp.pdf"
    tmp.write_bytes(body)
    try:
        pages = ingest.count_pdf_pages(tmp)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"not a readable PDF: {e}")

    base_slug = slugify(name)
    slug, i = base_slug, 2
    while slug in cfg["docs"]:
        slug = f"{base_slug}-{i}"
        i += 1

    UPLOADS_DIR.mkdir(exist_ok=True)
    dest = UPLOADS_DIR / f"{slug}.pdf"
    tmp.replace(dest)

    cfg["docs"][slug] = {
        "doc_id": f"handbook_{slug.replace('-', '_')}",
        "source": name.strip(),
        "audience": audience.strip() or slug,
        "pdf_filename": f"uploads/{slug}.pdf",
        "page_label_offset": 0,
        "suppress_pages": [],
        "expected_pages_in": pages,
        "normalize": {"strip_line_patterns": [], "typos": {}},
        "chunker": {"footer_pattern": r"^\d{1,3}\s*$", "h1_x0": None, "missing_tables": [], "typos": {}},
    }
    write_config(cfg)
    return {"id": slug, "actual_pages": pages, **cfg["docs"][slug]}


@app.delete("/api/handbooks/{doc}")
def remove_handbook(doc: str):
    """Config removal is the authoritative step -- once it's written, the
    handbook is gone from every listing and gate. File cleanup below is
    best-effort: on Windows, a concurrent request (a review-item thumbnail,
    the review scan itself) can still have the PDF open via pdfplumber,
    which makes unlink() fail with a sharing violation. That must not turn
    into a 500 that leaves the user unsure whether anything happened."""
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    d = cfg["docs"].pop(doc)
    write_config(cfg)

    warnings = []
    pdf_path = pdf_path_for(d)
    if pdf_path.exists() and UPLOADS_DIR in pdf_path.parents:
        last_err = None
        for attempt in range(5):
            try:
                pdf_path.unlink()
                last_err = None
                break
            except OSError as e:
                last_err = e
                time.sleep(0.15)
        if last_err:
            warnings.append(f"couldn't delete {pdf_path.name} yet (likely still open in a "
                             f"request that started just before removal) -- {last_err}")

    shutil.rmtree(RAW_DIR / doc, ignore_errors=True)
    shutil.rmtree(CLEAN_DIR / doc, ignore_errors=True)
    try:
        chunk_file_for(d).unlink(missing_ok=True)
    except OSError as e:
        warnings.append(str(e))

    state = read_review_state()
    if state.pop(doc, None) is not None:
        write_review_state(state)

    return {"removed": doc, "warnings": warnings}


# --------------------------------------------------------------- config ---

@app.get("/api/config")
def get_config():
    return read_config()


class DocConfigUpdate(BaseModel):
    config: dict[str, Any]


@app.put("/api/docs/{doc}/config")
def put_doc_config(doc: str, body: DocConfigUpdate):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    try:
        validate_doc_config(body.config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    cfg["docs"][doc] = body.config
    write_config(cfg)
    return cfg["docs"][doc]


# ---------------------------------------------------------------- gates ---

@app.get("/api/gates")
def get_gates():
    cfg = read_config()
    gate1, gate3 = [], []
    for doc, d in cfg["docs"].items():
        pdf_path = pdf_path_for(d)
        if not pdf_path.exists():
            gate1.append({"doc": doc, "ok": False, "message": f"PDF not found: {d['pdf_filename']}", "log": ""})
        else:
            gate1.append({"doc": doc, **call_gate(ingest.gate_drift, doc, str(pdf_path), True)})
        pending = [i for i in review_items_for(doc, d) if i["status"] in ("pending", "needs_rerun")]
        gate3.append({"doc": doc, "ok": not pending, "pending": pending})
    gate2 = call_gate(ingest.gate_authored, CHUNKS_DIR, True)
    return {"gate1": gate1, "gate2": gate2, "gate3": gate3}


# ----------------------------------------------------------------- run ---

class RunRequest(BaseModel):
    docs: Optional[list[str]] = None
    skip_gates: bool = False


@app.post("/api/run")
def run_pipeline(req: RunRequest):
    cfg = read_config()
    docs = req.docs if req.docs is not None else list(cfg["docs"])
    unknown = [doc for doc in docs if doc not in cfg["docs"]]
    if unknown:
        raise HTTPException(400, f"unknown doc(s): {unknown}")

    RAW_DIR.mkdir(exist_ok=True)
    CLEAN_DIR.mkdir(exist_ok=True)
    CHUNKS_DIR.mkdir(exist_ok=True)

    result = {"gate1": [], "stages": [], "gate2": None, "success": True}

    for doc in docs:
        d = cfg["docs"][doc]
        pdf_path = pdf_path_for(d)
        if not pdf_path.exists():
            result["gate1"].append({"doc": doc, "ok": False,
                                     "message": f"PDF not found: {d['pdf_filename']}", "log": ""})
            result["success"] = False
            continue
        g = {"doc": doc, **call_gate(ingest.gate_drift, doc, str(pdf_path), not req.skip_gates)}
        result["gate1"].append(g)
        if not g["ok"]:
            result["success"] = False

    if not result["success"] and not req.skip_gates:
        # LOUD, on purpose: ingest.py refuses to extract past a failed drift
        # gate rather than silently producing a wrong-but-clean-looking corpus.
        return result

    for doc in docs:
        d = cfg["docs"][doc]
        pdf_path = pdf_path_for(d)
        if not pdf_path.exists():
            continue
        raw_out, clean_out = RAW_DIR / doc, CLEAN_DIR / doc
        for stage_result in (
            run_stage("extract.py", pdf_path, "--out", raw_out),
            run_stage("normalize.py", raw_out, "--doc", doc, "--out", clean_out),
            run_stage("chunker.py", raw_out, "--doc", doc, "--out", CHUNKS_DIR,
                      "--authored", AUTHORED_PATH),
        ):
            stage_result["doc"] = doc
            result["stages"].append(stage_result)
            if not stage_result["ok"]:
                result["success"] = False

    g2 = call_gate(ingest.gate_authored, CHUNKS_DIR, not req.skip_gates)
    result["gate2"] = g2
    if not g2["ok"]:
        result["success"] = False

    result["gate3"] = []
    for doc in docs:
        d = cfg["docs"][doc]
        pending = [i for i in review_items_for(doc, d) if i["status"] in ("pending", "needs_rerun")]
        g3 = {"doc": doc, "ok": not pending, "pending": pending}
        result["gate3"].append(g3)
        if pending and not req.skip_gates:
            result["success"] = False

    return result


# --------------------------------------------------------------- chunks ---

@app.get("/api/chunks/{doc}")
def get_chunks(doc: str, q: Optional[str] = None):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    chunks = load_chunks(cfg["docs"][doc])
    if q:
        needle = q.lower()
        chunks = [c for c in chunks
                  if needle in c.get("chunk_text", "").lower() or needle in c.get("title", "").lower()]
    return chunks


@app.get("/api/chunks/{doc}/download")
def download_chunks(doc: str):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    f = chunk_file_for(cfg["docs"][doc])
    if not f.exists():
        raise HTTPException(404, "no chunks yet -- run the pipeline first")
    return FileResponse(f, media_type="application/jsonl", filename=f.name)


# ---------------------------------------------------------- authored tables ---

@app.get("/api/authored-tables")
def get_authored_tables():
    if not AUTHORED_PATH.exists():
        return []
    return json.loads(AUTHORED_PATH.read_text(encoding="utf-8"))


REQUIRED_AUTHORED_FIELDS = ("doc_id", "authored_id", "title", "breadcrumb", "text", "verified")


class AuthoredTablesUpdate(BaseModel):
    tables: list[dict[str, Any]]


@app.put("/api/authored-tables")
def put_authored_tables(body: AuthoredTablesUpdate):
    for i, t in enumerate(body.tables):
        missing = [k for k in REQUIRED_AUTHORED_FIELDS if k not in t]
        if missing:
            raise HTTPException(400, f"authored table #{i}: missing field(s) {missing}")
    tmp = AUTHORED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body.tables, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(AUTHORED_PATH)
    return body.tables


# ------------------------------------------------------------- review queue ---

@app.get("/api/docs/{doc}/review")
def get_review(doc: str):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    return review_items_for(doc, cfg["docs"][doc])


class ReviewUpdate(BaseModel):
    status: str  # "cleared" or "pending"
    note: str = ""
    by: str = ""


@app.put("/api/docs/{doc}/review/{page}")
def set_review_status(doc: str, page: int, body: ReviewUpdate):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    if body.status not in ("cleared", "pending"):
        raise HTTPException(400, "status must be 'cleared' or 'pending'")

    state = read_review_state()
    doc_state = state.setdefault(doc, {})
    if body.status == "cleared":
        doc_state[str(page)] = {"note": body.note, "by": body.by,
                                 "on": datetime.date.today().isoformat()}
    else:
        doc_state.pop(str(page), None)
    write_review_state(state)
    return {"doc": doc, "page": page, "status": body.status}


# -------------------------------------------------------------- page review ---

@app.get("/api/docs/{doc}/pages")
def list_pages(doc: str):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    d = cfg["docs"][doc]
    raw_dir = RAW_DIR / doc
    pages = sorted((int(p.stem) for p in raw_dir.glob("*.txt")) if raw_dir.exists() else [])
    missing_tables = set(d["chunker"]["missing_tables"])
    suppressed = set(d["suppress_pages"])
    return [{"page": n, "suppressed": n in suppressed, "missing_table": n in missing_tables}
            for n in pages]


@app.get("/api/docs/{doc}/pages/{n}/text")
def page_text(doc: str, n: int):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    txt_path = RAW_DIR / doc / f"{n}.txt"
    spans_path = RAW_DIR / doc / f"{n}.spans.json"
    if not txt_path.exists():
        raise HTTPException(404, f"no extracted page {n} for {doc} -- run the pipeline first")
    return {
        "text": txt_path.read_text(encoding="utf-8"),
        "spans": json.loads(spans_path.read_text(encoding="utf-8")) if spans_path.exists() else [],
    }


# pdfplumber's to_image() rasterizes via pypdfium2, a ctypes wrapper around
# PDFium's native library. PDFium is not safe for concurrent access from
# multiple threads, even across independently-opened documents -- the review
# queue's page loads several thumbnails at once, which FastAPI dispatches to
# separate worker threads, and PDFium raises "Failed to load page." under
# that race. Serializing every render behind one lock avoids the crash; a
# single page renders in well under a second, so queuing has no real cost.
_PDF_RENDER_LOCK = threading.Lock()


@app.get("/api/docs/{doc}/pages/{n}/image")
def page_image(doc: str, n: int):
    cfg = read_config()
    if doc not in cfg["docs"]:
        raise HTTPException(404, f"unknown doc {doc!r}")
    pdf_path = pdf_path_for(cfg["docs"][doc])
    if not pdf_path.exists():
        raise HTTPException(404, f"no PDF uploaded for {doc}")
    import pdfplumber
    with _PDF_RENDER_LOCK, pdfplumber.open(pdf_path) as pdf:
        if not (1 <= n <= len(pdf.pages)):
            raise HTTPException(404, f"page {n} out of range (1..{len(pdf.pages)})")
        img = pdf.pages[n - 1].to_image(resolution=150)
        buf = io.BytesIO()
        img.original.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ---------------------------------------------------------------- static ---

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
