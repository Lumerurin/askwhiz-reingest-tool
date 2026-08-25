#!/usr/bin/env python3
"""
chunker.py — AskWhiz v2, handbook corpus, stage 4 of 6.

    extract.py  ->  CHUNKER  ->  hand-authored table chunks  ->  freeze to JSONL

Consumes raw/<doc>/*.spans.json (from extract.py, run against the real PDFs)
and emits chunks in the v1 schema, one JSON object per line.

What it does, in order:
  1. Drops footer lines and suppressed pages.
  2. Detects headings from font weight + position (never from text case).
  3. Merges headings that wrapped across two lines.
  4. Assembles sections ACROSS page boundaries -- a section ends at the next
     heading of equal-or-higher level, not at the end of a page.
  5. Reflows body text using the wrapped flag; never joins into a list item.
  6. Applies the character normalization and typo fixes.
  7. Merges sections under ~50 words into the previous sibling under the same
     parent -- or keeps them, flagged short_section, if no sibling exists.
  8. Prepends the breadcrumb to chunk_text BEFORE embedding:
       [College Student Handbook > Tuition Fees & Other Charges > General Information]
  9. Emits corpus statistics, and FAILS if any chunk promises a table that
     did not extract.

    python chunker.py raw/college --doc college --out chunks
    python chunker.py raw/shs     --doc shs     --out chunks
"""

import argparse
import collections
import json
import pathlib
import re
import statistics
import sys
import unicodedata

# ---------------------------------------------------------------- config ---

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "pipeline_config.json"

# Per-document configuration lives in pipeline_config.json (shared with
# normalize.py and ingest.py) so it can be edited from the app without
# touching this file.
#
# h1_x0: H1 = the white-on-blue section bands (inset ~83.7) and the lettered
# scholarship headings (90) for the college handbook. H2 = flush at the 72pt
# margin. SHS H1s are 16pt Lucida / centered-large instead, so its h1_x0 is
# null.
#
# missing_tables: pages where verify_extraction.py proved a table exists on
# the page image but did NOT extract (college p21: the GWA/academic-status
# table is a pasted screenshot). Any section touching one of these pages
# while promising a table FAILS the build unless an authored chunk covers it.


def load_config(doc, config_path=DEFAULT_CONFIG):
    all_cfg = json.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    docs = all_cfg["docs"]
    if doc not in docs:
        raise SystemExit(f"unknown doc {doc!r} in {config_path}; choices: {sorted(docs)}")
    d = docs[doc]
    sub = d["chunker"]
    return {
        "source": d["source"],
        "doc_id": d["doc_id"],
        "audience": d["audience"],
        "page_label_offset": d["page_label_offset"],
        "suppress_pages": d["suppress_pages"],
        "footer": re.compile(sub["footer_pattern"]),
        "h1_x0": tuple(sub["h1_x0"]) if sub["h1_x0"] else None,
        "missing_tables": sub["missing_tables"],
        "typos": {int(page): [tuple(pair) for pair in fixes]
                  for page, fixes in sub["typos"].items()},
    }

PUNCT = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
         "\u2013": "-", "\u2014": "-", "\u2011": "-", "\u00a0": " ",
         "\u0002": "-", "\u2028": ""}
BULLET_CHARS = "\u2022\uf0a7\uf0b7\u00bb"
LIST_START = re.compile(
    r"^\s*(?:[" + BULLET_CHARS + r"]|\(cid:\d+\)|\d{1,2}\.\s|[a-z]\.\s|\([a-z0-9]{1,3}\)\s)")
CID = re.compile(r"\(cid:\d+\)")
TABLE_PROMISE = re.compile(
    r"table below|following table|table shows|shown in the table|as indicated in the table",
    re.IGNORECASE)

# ------------------------------------------------------------- load spans ---

def load_pages(indir):
    pages = {}
    for p in sorted(pathlib.Path(indir).glob("*.spans.json"),
                    key=lambda p: int(p.stem.split(".")[0])):
        pages[int(p.stem.split(".")[0])] = json.loads(p.read_text(encoding="utf-8"))
    return pages


# -------------------------------------------------------- heading detection ---

def doc_metrics(pages):
    sizes = collections.Counter()
    x0s = collections.Counter()
    for spans in pages.values():
        for s in spans:
            if s["size"]:
                sizes[round(s["size"], 1)] += len(s["text"])
            x0s[round(s["x0"])] += 1
    return sizes.most_common(1)[0][0], x0s.most_common(1)[0][0]


def is_heading(s, body, margin, cfg):
    t = s["text"].strip()
    if not t or cfg["footer"].match(t):
        return False
    if not s["bold"] or s.get("italic") or s.get("in_table"):
        return False
    if len(t.split()) > 12 or t[-1] in ".,;":
        return False
    near_margin = s["x0"] <= margin + 40
    larger = s["size"] and round(s["size"], 1) >= body + 0.5
    return near_margin or larger


def heading_level(s, body, cfg):
    size = round(s["size"], 1) if s["size"] else 0
    if size >= body + 0.5:
        return 1
    if cfg["h1_x0"] and cfg["h1_x0"][0] <= s["x0"] <= cfg["h1_x0"][1]:
        return 1
    return 2


def merge_wrapped_headings(items):
    """A heading that wrapped across two PDF lines arrives as two consecutive
    heading items. Join them: the first has no terminal punctuation and the
    second continues it (lowercase start, opening paren, or plain ALL-CAPS
    continuation like 'DISABILITIES')."""
    out = []
    for it in items:
        if (out and out[-1]["kind"] == "heading" and it["kind"] == "heading"
                and out[-1]["page"] == it["page"]
                and out[-1]["level"] == it["level"]
                and not out[-1]["text"].rstrip().endswith((":", ")", '"'))
                and (it["text"][:1].islower() or it["text"][:1] == "("
                     or (out[-1]["text"].isupper() and it["text"].isupper()
                         and len(it["text"].split()) <= 4))):
            out[-1]["text"] = out[-1]["text"].rstrip() + " " + it["text"].lstrip()
        else:
            out.append(it)
    return out


# ----------------------------------------------------------- text cleanup ---

def clean(text):
    for a, b in PUNCT.items():
        text = text.replace(a, b)
    text = CID.sub("-", text) if CID.match(text.lstrip()) else CID.sub("", text)
    for ch in BULLET_CHARS:
        text = text.replace(ch, "\n- ")           # mid-line bullets split items
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def reflow(lines):
    """lines: list of (text, wrapped). Joins soft-wrapped lines; never joins
    into a list item."""
    out, buf = [], ""
    for i, (text, wrapped) in enumerate(lines):
        nxt = lines[i + 1][0] if i + 1 < len(lines) else ""
        if LIST_START.match(nxt):
            wrapped = False
        if wrapped:
            buf += text.rstrip() + " "
        else:
            out.append((buf + text).strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return "\n".join(l for l in out if l)


# --------------------------------------------------------------- sections ---

def build_sections(pages, cfg):
    body, margin = doc_metrics(pages)

    items = []
    for page, spans in pages.items():
        if page in cfg["suppress_pages"]:
            continue
        for s in spans:
            t = s["text"].strip()
            if not t or cfg["footer"].match(t):
                continue
            if is_heading(s, body, margin, cfg):
                items.append({"kind": "heading", "text": t, "page": page,
                              "level": heading_level(s, body, cfg)})
            else:
                items.append({"kind": "body", "text": s["text"],
                              "wrapped": s["wrapped"], "page": page})

    items = merge_wrapped_headings(items)

    sections = []
    cur = {"h1": None, "h2": None, "lines": [], "pages": set(), "level": 0}

    def flush():
        if cur["lines"] or cur["h2"] or cur["h1"]:
            sections.append({
                "h1": cur["h1"], "h2": cur["h2"],
                "pages": sorted(cur["pages"]),
                "text": reflow(cur["lines"]),
            })

    for it in items:
        if it["kind"] == "heading":
            flush()
            if it["level"] == 1:
                cur = {"h1": it["text"], "h2": None,
                       "lines": [], "pages": {it["page"]}, "level": 1}
            else:
                cur = {"h1": cur["h1"], "h2": it["text"],
                       "lines": [], "pages": {it["page"]}, "level": 2}
        else:
            cur["lines"].append((it["text"], it["wrapped"]))
            cur["pages"].add(it["page"])
    flush()
    return sections


def apply_typos(sections, cfg):
    applied, missed = 0, []
    wanted = {w: p for p, fixes in cfg["typos"].items() for w, _ in fixes}
    fixes = [(w, r) for fx in cfg["typos"].values() for w, r in fx]
    for sec in sections:
        for wrong, right in fixes:
            if wrong in sec["text"]:
                sec["text"] = sec["text"].replace(wrong, right); applied += 1
            if sec["h2"] and wrong in sec["h2"]:
                sec["h2"] = sec["h2"].replace(wrong, right); applied += 1
            if sec["h1"] and wrong in sec["h1"]:
                sec["h1"] = sec["h1"].replace(wrong, right); applied += 1
    for wrong in wanted:
        if not any(wrong in (s["text"] + str(s["h1"]) + str(s["h2"])) for s in sections):
            pass  # applied earlier in another section, or genuinely missing
    return applied


def merge_short(sections, min_words=50):
    """Merge a short section into the previous section under the same H1."""
    out, merged = [], 0
    for sec in sections:
        words = len(sec["text"].split())
        if (out and words < min_words and sec["h1"] == out[-1]["h1"]
                and sec["text"].strip()):
            prev = out[-1]
            label = f"\n\n{sec['h2']}\n" if sec["h2"] else "\n\n"
            prev["text"] = prev["text"].rstrip() + label + sec["text"]
            prev["pages"] = sorted(set(prev["pages"]) | set(sec["pages"]))
            merged += 1
        else:
            out.append(sec)
    return out, merged


# ------------------------------------------------------------------ emit ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", default="chunks")
    ap.add_argument("--min-words", type=int, default=50)
    ap.add_argument("--authored", help="JSON file of hand-authored chunks to append")
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                     help="pipeline_config.json path (default: alongside this script)")
    args = ap.parse_args()

    cfg = load_config(args.doc, args.config)
    pages = load_pages(args.indir)
    sections = build_sections(pages, cfg)

    for sec in sections:
        sec["text"] = clean(sec["text"])
        if sec["h1"]: sec["h1"] = clean(sec["h1"])
        if sec["h2"]: sec["h2"] = clean(sec["h2"])

    typo_count = apply_typos(sections, cfg)
    sections = [s for s in sections if s["text"].strip()]
    sections, merged = merge_short(sections, args.min_words)

    outdir = pathlib.Path(args.out); outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / f"{cfg['doc_id']}.chunks.jsonl"

    failures, chunks, token_counts = [], [], []
    for i, sec in enumerate(sections):
        title = sec["h2"] or sec["h1"] or cfg["source"]
        crumb_parts = [cfg["source"]] + [p for p in (sec["h1"], sec["h2"]) if p]
        breadcrumb = "[" + " > ".join(crumb_parts) + "]"
        text = breadcrumb + "\n" + sec["text"]
        words = len(sec["text"].split())
        token_counts.append(int(words * 1.35))

        touches_missing = set(sec["pages"]) & set(cfg.get("missing_tables", []))
        if TABLE_PROMISE.search(sec["text"]) and touches_missing:
            failures.append(
                f"section '{title}' touches page(s) {sorted(touches_missing)} where a "
                f"table exists on the page image but did not extract. The chunk "
                f"promises a table it does not contain. Provide an authored "
                f"transcription via --authored.")

        chunks.append({
            "chunk_id": f"{cfg['doc_id']}_s{i:04d}",
            "source": cfg["source"],
            "document_type": "handbook",
            "source_type": "pdf_extraction",
            "audience": cfg["audience"],
            "title": title,
            "breadcrumb": breadcrumb,
            "h1": sec["h1"], "h2": sec["h2"],
            "pages": sec["pages"],
            "page_labels": [p + cfg["page_label_offset"] for p in sec["pages"]],
            "short_section": words < args.min_words,
            "token_count": token_counts[-1],
            "chunk_text": text,
        })

    authored = []
    if args.authored:
        for a in json.loads(pathlib.Path(args.authored).read_text(encoding="utf-8")):
            if a.get("doc_id") != cfg["doc_id"]:
                continue
            a.setdefault("source", cfg["source"])
            a.setdefault("audience", cfg["audience"])
            a.setdefault("document_type", "handbook")
            a["source_type"] = "table_transcription"
            a["chunk_id"] = f"{cfg['doc_id']}_authored_{a['authored_id']}"
            a["chunk_text"] = a["breadcrumb"] + "\n" + a["text"]
            a["token_count"] = int(len(a["text"].split()) * 1.35)
            a["short_section"] = False
            authored.append(a)
            covered = set(a.get("covers_pages", [])) & set(cfg.get("missing_tables", []))
            if covered:
                failures = [f for f in failures
                            if not any(f"page(s) [{p}]" in f or f"page(s) {sorted(covered)}" in f
                                       for p in covered)]
    chunks.extend(authored)

    with outfile.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    short = sum(c["short_section"] for c in chunks)
    print(f"\n{cfg['source']}")
    print(f"  sections: {len(chunks)}   merged-in short sections: {merged}   typo fixes: {typo_count}")
    print(f"  tokens: median {int(statistics.median(token_counts))}, "
          f"p90 {int(statistics.quantiles(token_counts, n=10)[8])}, max {max(token_counts)}")
    print(f"  chunks under {args.min_words} words after merging: {short}")
    print(f"  -> {outfile}")

    if failures:
        print("\nFAILURES -- do not freeze this corpus:")
        for x in failures:
            print(f"  ! {x}")
        sys.exit(1)


if __name__ == "__main__":
    main()
