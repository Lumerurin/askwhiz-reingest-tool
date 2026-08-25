#!/usr/bin/env python3
"""
ingest.py — AskWhiz v2, handbook corpus, one-command re-ingest.

    ingest.py  ==  extract -> normalize -> chunk -> [GATE] -> embed(stub) -> [GATE]

Runs the whole handbook pipeline end to end for BOTH handbooks, enforces the
drift gates that don't need AWS, and stops at a clearly marked embed stub.

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

WHAT IT DOES NOT DO
    It does not embed. The AWS account does not exist yet, and even once it
    does, S3 Vectors freezes an index's dimension and filterable-metadata keys
    at creation time -- decisions that belong to a human, not a wrapper. The
    embed step is a stub with the exact Bedrock + s3vectors calls documented
    inside it, ready to fill when the account is live. Until then, ingest.py
    produces the frozen chunks and stops -- which is genuinely the whole
    knowledge-base-making job minus the final upload.

USAGE
    python ingest.py --pdf-college MCM-HANDBOOK-2020-COLLEGE.pdf \
                     --pdf-shs     MCM-HANDBOOK-2020-SHS.pdf \
                     --authored    authored_tables.json \
                     --workdir     build

    Add --embed once AWS exists and the stub is filled (it will refuse until
    then). Add --skip-gates only to reproduce a known-good build; never on a
    new edition.

REQUIREMENTS
    pip install pdfplumber
    extract.py, normalize.py, chunker.py in the same directory.
"""

import argparse
import json
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


def embed_stub(chunks_dir, do_embed):
    """The one AWS-touching step. Deliberately unimplemented.

    When the AWS account exists, fill this in. Confirmed from AWS docs
    (Dec 2025 S3 Vectors GA, available in ap-southeast-1):

      1. For each chunk, compute its embedding:
           bedrock-runtime invoke_model
             model_id = "amazon.titan-embed-text-v2:0"
             body = {"inputText": chunk["chunk_text"], "dimensions": DIM}
         DIM is 1024 or 512 -- but S3 Vectors FREEZES dimension at index
         creation, so a 512 experiment is a SEPARATE index, not a re-config.
         Titan throttles on requests/minute, not tokens: batch + backoff.
         132 chunks is fine; BoldDesk's thousands will throttle -- build the
         backoff now.

      2. Create (or reuse) the vector index with:
           - dimension = DIM
           - 'audience' declared as a FILTERABLE metadata key (college/shs
             pre-filter; frozen at creation, cannot add later)
           - embedding_model + dimensions recorded in index metadata

      3. PUT each vector with its metadata (audience, chunk_id, page_labels,
         source_type, breadcrumb) via s3vectors put-vectors.

      4. Do NOT flip the live alias here. Build a NEW versioned index, run the
         gold smoke test against it, and flip the alias as a deliberate human
         step only after the DPO sign-off exists.
    """
    if not do_embed:
        print("\n  embed: SKIPPED (stub). Corpus is built and frozen; the only "
              "remaining step is uploading vectors to S3 Vectors, which needs "
              "the AWS account. See embed_stub() for the exact calls.")
        return
    raise SystemExit(
        "\n! --embed was requested but the embed step is still a stub.\n"
        "  Fill in embed_stub() with the Bedrock + s3vectors calls once the\n"
        "  AWS account exists. Refusing to pretend an index was built.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf-college", required=True)
    ap.add_argument("--pdf-shs", required=True)
    ap.add_argument("--authored", required=True)
    ap.add_argument("--workdir", default="build")
    ap.add_argument("--embed", action="store_true",
                    help="upload to S3 Vectors (refuses until stub is filled)")
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

    embed_stub(chunks, args.embed)

    print("\nDone. Frozen corpus is in", chunks)
    if gates_on:
        print("Both gates passed. Safe to commit the chunks and (when AWS exists) embed.")


if __name__ == "__main__":
    main()
