#!/usr/bin/env python3
"""
ingest.py — AskWhiz v2, handbook corpus, one-command re-ingest.

    ingest.py  ==  [GATE 1] -> extract -> normalize -> chunk -> [GATE 2]
                   -> (with --embed) embed + index -> [GATE 4]

Runs the whole handbook pipeline end to end for BOTH handbooks, enforces the
gates, and with --embed builds the search index and checks it against the gold
questions. (GATE 3, the page-review queue, lives in the web app: app.py.)

WHY THIS EXISTS
    You asked to package the pipeline so a new handbook edition can be fed in.
    This is that package. But a naive "PDF in, index out" wrapper is dangerous
    here: the per-document CONFIG in extract/normalize/chunker is hand-tuned to
    the 2020 PDFs (suppress_pages, page_label_offset, the running-header regex,
    the typo tables, the four verified table transcriptions). A 2026 edition
    with a different page count silently breaks all of it and still produces a
    clean-looking, WRONG corpus. So this script is built to be LOUD and to
    REFUSE, not smooth. When a gate fires, a human edits CONFIG and re-verifies
    the tables. The app makes that human work findable and required, not
    optional.

EMBED + INDEX (no cloud)
    AWS was dropped (Oct 2026). Steps 7-8 run locally: build_index.py splits
    each chunk into small labelled passages, embeds them with a small local
    model (bge-small-en-v1.5 via fastembed), and writes an index folder the
    chatbot loads at startup. It always writes a NEW folder. Nothing here
    publishes it: a person copies a passing index into the chatbot repo.
    The app's "Search Index" tab does the same build + GATE 4 with a button.

USAGE
    python ingest.py --pdf-college MCM-HANDBOOK-2020-COLLEGE.pdf \
                     --pdf-shs     MCM-HANDBOOK-2020-SHS.pdf \
                     --authored    authored_tables.json \
                     --workdir     build --embed

    Leave out --embed to stop after the chunks. Add --skip-gates only to
    reproduce a known-good build; never on a new edition.

REQUIREMENTS
    pip install -r requirements.txt
    extract.py, normalize.py, chunker.py, passages.py, build_index.py,
    retrieve.py, eval_gold.py and gold_expected.json in the same directory.
"""

import argparse
import json
import math
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "pipeline_config.json"


def load_pipeline_config(config_path=DEFAULT_CONFIG):
    """EXPECTED and REQUIRED_AUTHORED both come from pipeline_config.json
    (shared with normalize.py and chunker.py) so the app can edit them in one
    place. EXPECTED is what CONFIG in the sub-scripts was tuned against -- if
    a new PDF's shape differs from this, the gates fire. Update these numbers
    ONLY after re-verifying the corresponding config entries by hand against
    the new printed pages. REQUIRED_AUTHORED lists the hand-transcribed table
    chunks that MUST be present and verified before a corpus is allowed to
    freeze."""
    all_cfg = json.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    expected = {
        doc: {
            "pages_in": d["expected_pages_in"],
            "suppress": d["suppress_pages"],
            "page_label_offset": d["page_label_offset"],
        }
        for doc, d in all_cfg["docs"].items()
    }
    return expected, all_cfg["required_authored"]


EXPECTED, REQUIRED_AUTHORED = load_pipeline_config()


def run(cmd):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        raise SystemExit(f"step failed: {' '.join(str(c) for c in cmd)}")
    return r.stdout


def count_pdf_pages(pdf):
    import pdfplumber
    with pdfplumber.open(pdf) as p:
        return len(p.pages)


def gate_drift(doc, pdf, gates_on):
    """GATE 1 — before trusting extraction, confirm the new PDF matches what
    CONFIG expects. A page-count change means suppress_pages and
    page_label_offset are probably wrong now."""
    n = count_pdf_pages(pdf)
    exp = EXPECTED[doc]["pages_in"]
    if n != exp:
        msg = (f"GATE 1 (drift): {doc} PDF has {n} pages; CONFIG expects {exp}. "
               f"A different page count means suppress_pages "
               f"{EXPECTED[doc]['suppress']} and page_label_offset "
               f"{EXPECTED[doc]['page_label_offset']} are likely wrong. "
               f"Re-verify the CONFIG entry for '{doc}' in extract.py, "
               f"normalize.py and chunker.py against the new printed pages, "
               f"update EXPECTED here, then re-run.")
        if gates_on:
            raise SystemExit("\n! " + msg)
        print("\n! WARNING (gate skipped): " + msg)
    else:
        print(f"  gate 1 ok: {doc} PDF page count {n} matches CONFIG.")


def gate_authored(chunks_dir, gates_on):
    """GATE 2 — after chunking, confirm every required table transcription is
    present AND marked verified. A verified:false chunk (or a missing one) means
    a human hasn't checked it against the printed page for THIS edition."""
    problems = []
    for doc_id, ids in REQUIRED_AUTHORED.items():
        f = chunks_dir / f"{doc_id}.chunks.jsonl"
        if not f.exists():
            problems.append(f"{doc_id}: chunk file missing")
            continue
        chunks = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        authored = {c["chunk_id"]: c for c in chunks
                    if c.get("source_type") == "table_transcription"}
        for aid in ids:
            cid = f"{doc_id}_authored_{aid}"
            c = authored.get(cid)
            if c is None:
                problems.append(f"{doc_id}: required table '{aid}' not in corpus")
            elif not c.get("verified"):
                problems.append(
                    f"{doc_id}: table '{aid}' is present but verified=false "
                    f"(by={c.get('verified_by') or 'nobody'}). Re-verify against "
                    f"the printed page for this edition before freezing.")
    if problems:
        head = "GATE 2 (authored tables): unverified or missing transcriptions."
        if gates_on:
            raise SystemExit("\n! " + head + "\n    - " + "\n    - ".join(problems))
        print("\n! WARNING (gate skipped): " + head)
        for p in problems:
            print("    - " + p)
    else:
        print("  gate 2 ok: all required table transcriptions present and verified.")


# GATE 4 thresholds. The motto question is the v1 bug that must never return.
# 90% of the gold questions must be in the top 5 (18 of 20, 23 of 25, ...).
GOLD_MIN_SHARE = 0.9
GOLD_MUST_BE_FIRST = ["What is the school motto?"]   # checked for every audience it's asked for


def gold_min_hit5(n):
    return math.ceil(GOLD_MIN_SHARE * n - 1e-9)


def gold_gate_problems(results):
    """GATE 4 rule, shared by this script and the app. `results` is the list
    eval_gold.py saves: one {"q", "rank"} per gold question (rank None = not
    in the top 5). Returns a list of problems; empty means the gate passes."""
    problems = []
    hit5 = sum(1 for r in results if r["rank"])
    need = gold_min_hit5(len(results))
    if hit5 < need:
        problems.append(f"only {hit5} of {len(results)} gold questions found in the "
                        f"top 5 (need {need})")
    for r in results:
        if r["q"] in GOLD_MUST_BE_FIRST and r["rank"] != 1:
            who = f" ({r['audience']})" if r.get("audience") else ""
            problems.append(f"'{r['q']}'{who} must rank its answer first; got "
                            f"{'not in top 5' if r['rank'] is None else 'rank ' + str(r['rank'])}")
    return problems


def build_and_check_index(chunk_files, index_dir, gold_path, gates_on, no_dense=False):
    """Steps 7-8 (embed + index) and GATE 4 (gold smoke test).

    Builds a NEW index folder with build_index.py, then scores it with
    eval_gold.py. The chatbot only uses this folder after a person copies it
    into the chatbot repo, and only if this passes."""
    index_dir = pathlib.Path(index_dir)
    cmd = [sys.executable, HERE / "build_index.py", "--chunks", *chunk_files, "--out", index_dir]
    if no_dense:
        cmd.append("--no-dense")
    print(run(cmd))

    print("=== GATE 4: gold smoke test (post-index) ===")
    report = index_dir / "gold.json"
    cmd = [sys.executable, HERE / "eval_gold.py", "--index", index_dir,
           "--gold", gold_path, "--save", report]
    if no_dense:
        cmd.append("--no-dense")
    run(cmd)
    data = json.loads(report.read_text(encoding="utf-8"))
    s = data["summary"]
    print(f"  top 1: {s['hit@1']}/{s['n']}   top 3: {s['hit@3']}/{s['n']}   "
          f"top 5: {s['hit@5']}/{s['n']}   MRR {s['mrr']:.3f}")
    problems = gold_gate_problems(data["results"])
    if problems:
        msg = ("GATE 4 (gold smoke test): " + "; ".join(problems) +
               ". Do NOT use this index. See " + str(report) + " for details.")
        if gates_on:
            raise SystemExit("\n! " + msg)
        print("\n! WARNING (gate skipped): " + msg)
    else:
        print("  gate 4 ok: search finds the gold answers.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-college", required=True)
    ap.add_argument("--pdf-shs", required=True)
    ap.add_argument("--authored", required=True)
    ap.add_argument("--workdir", default="build")
    ap.add_argument("--embed", action="store_true",
                    help="also build the search index (steps 7-8) and run GATE 4")
    ap.add_argument("--index-dir", default=None,
                    help="where to write the index (default: <workdir>/index)")
    ap.add_argument("--gold", default=str(HERE / "gold_expected.json"))
    ap.add_argument("--no-dense", action="store_true",
                    help="keyword-only index, for testing without the embedding model")
    ap.add_argument("--skip-gates", action="store_true",
                    help="reproduce a known-good build only; never on a new edition")
    args = ap.parse_args()

    gates_on = not args.skip_gates
    work = pathlib.Path(args.workdir)
    raw, clean, chunks = work / "raw", work / "clean", work / "chunks"
    for d in (raw, clean, chunks):
        d.mkdir(parents=True, exist_ok=True)

    docs = [("college", args.pdf_college), ("shs", args.pdf_shs)]

    print("=== GATE 1: drift check (pre-extraction) ===")
    for doc, pdf in docs:
        if not pathlib.Path(pdf).exists():
            raise SystemExit(f"! PDF not found: {pdf}")
        gate_drift(doc, pdf, gates_on)

    print("\n=== extract -> normalize -> chunk ===")
    for doc, pdf in docs:
        print(f"\n[{doc}]")
        run([sys.executable, HERE / "extract.py", pdf, "--out", raw / doc])
        run([sys.executable, HERE / "normalize.py", raw / doc, "--doc", doc,
             "--out", clean / doc])
        run([sys.executable, HERE / "chunker.py", raw / doc, "--doc", doc,
             "--out", chunks, "--authored", args.authored])

    print("\n=== GATE 2: authored-table verification (post-chunk) ===")
    gate_authored(chunks, gates_on)

    total = 0
    for f in chunks.glob("*.chunks.jsonl"):
        n = sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip())
        total += n
        print(f"  {f.name}: {n} chunks")
    print(f"  total: {total} chunks in {chunks}/")

    if args.embed:
        print("\n=== embed + index (steps 7-8) ===")
        index_dir = pathlib.Path(args.index_dir) if args.index_dir else work / "index"
        files = sorted(str(f) for f in chunks.glob("*.chunks.jsonl"))
        build_and_check_index(files, index_dir, args.gold, gates_on, args.no_dense)
        print("\nDone. Chunks in", chunks, "| search index in", index_dir)
    else:
        print("\n  embed: skipped (add --embed to build the search index).")
        print("\nDone. Frozen corpus is in", chunks)


if __name__ == "__main__":
    main()
