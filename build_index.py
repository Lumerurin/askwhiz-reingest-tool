#!/usr/bin/env python3
"""
build_index.py - steps 7 and 8 of the knowledge base: embed + index.

    pip install -r requirements.txt
    python build_index.py --chunks chunks/handbook_college.chunks.jsonl chunks/handbook_shs.chunks.jsonl --out index

Reads the FROZEN chunk files (never edits them), splits each chunk into small
labelled passages (see passages.py), embeds every passage with a small local
model, and writes the index folder the chatbot loads at startup:

    index/manifest.json   model, dimensions, counts, corpus fingerprint
    index/chunks.json     the chunks (what the AI answers from + citations)
    index/passages.json   the search passages
    index/vectors.npy     one normalised vector per passage

EMBEDDING MODEL: BAAI/bge-small-en-v1.5 through fastembed.
    - Runs on a normal CPU, no GPU, no API key, no per-use cost.
    - Small (~130 MB on disk), so it fits on Render's 512 MB Starter plan
      alongside the app.
    - The SAME model must embed questions at answer time; the manifest records
      it, and retrieve.py loads whatever the manifest names.
    Swapping the model later = rebuild the index with --model (a rebuild, not a
    rewrite). Measure any swap against the gold questions first (rule 1).

Never overwrites a live index in place: writes to --out, so build into a new
folder (index_v2), run eval_gold.py on it, then point the app at it.
"""

import argparse
import datetime
import hashlib
import json
import statistics
import sys
from pathlib import Path

import numpy as np

from passages import split_chunk

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


def load_chunks(paths):
    chunks = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(json.loads(line))
    ids = [c["chunk_id"] for c in chunks]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"! duplicate chunk ids: {sorted(dupes)}")
    for c in chunks:
        for key in ("chunk_id", "audience", "breadcrumb", "chunk_text", "pages", "page_labels"):
            if key not in c:
                raise SystemExit(f"! chunk {c.get('chunk_id')} is missing '{key}'")
        if not isinstance(c["audience"], str) or not c["audience"].strip():
            raise SystemExit(f"! chunk {c['chunk_id']} has no audience")
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", nargs="+", required=True)
    ap.add_argument("--out", default="index")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--no-dense", action="store_true",
                    help="skip embeddings (keyword search only) - for testing without the model")
    args = ap.parse_args()

    chunks = load_chunks(args.chunks)
    passages = [p for c in chunks for p in split_chunk(c)]

    per_parent = {}
    for p in passages:
        per_parent[p["parent_id"]] = per_parent.get(p["parent_id"], 0) + 1
    missing = [c["chunk_id"] for c in chunks if c["chunk_id"] not in per_parent]
    if missing:
        raise SystemExit(f"! chunks with no passages (would be unsearchable): {missing}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    corpus_hash = hashlib.sha256(
        "\n".join(json.dumps(c, sort_keys=True, ensure_ascii=False) for c in chunks).encode()
    ).hexdigest()

    manifest = {
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "model": None if args.no_dense else args.model,
        "dims": None,
        "chunk_count": len(chunks),
        "passage_count": len(passages),
        "audiences": {a: sum(c["audience"] == a for c in chunks)
                      for a in sorted({c["audience"] for c in chunks})},
        "corpus_sha256": corpus_hash,
        "source_files": [str(p) for p in args.chunks],
    }

    if not args.no_dense:
        from fastembed import TextEmbedding
        model = TextEmbedding(args.model)
        texts = [p["index_text"] for p in passages]
        vecs = np.asarray(list(model.passage_embed(texts)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        if len(vecs) != len(passages):
            raise SystemExit("! embedding count does not match passage count")
        np.save(out / "vectors.npy", vecs)
        manifest["dims"] = int(vecs.shape[1])

    chunk_map = {c["chunk_id"]: {k: c[k] for k in
                 ("chunk_id", "audience", "breadcrumb", "pages", "page_labels",
                  "chunk_text", "source_type")} for c in chunks}
    (out / "chunks.json").write_text(json.dumps(chunk_map, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "passages.json").write_text(json.dumps(passages, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    words = [len(p["text"].split()) for p in passages]
    print(f"chunks:   {len(chunks)}  ({manifest['audiences']})")
    print(f"passages: {len(passages)}  words median {statistics.median(words):.0f}, "
          f"max {max(words)}, under 10 words: {sum(w < 10 for w in words)}")
    print(f"model:    {manifest['model'] or 'none (keyword only)'}  dims: {manifest['dims']}")
    print(f"index written to {out}/")


if __name__ == "__main__":
    sys.exit(main())
