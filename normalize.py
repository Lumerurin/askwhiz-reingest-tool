#!/usr/bin/env python3
"""
normalize.py — AskWhiz v2, handbook corpus, stage 2 of 4.

    extract (pdfplumber)  ->  NORMALIZE  ->  section chunker  ->  freeze to JSONL

The handbooks are STATIC. This script runs once. Its output is committed to git
and becomes the corpus. It is not a pipeline stage; it is a migration. Keep it
in the repo as the record of what was changed, and never point it at BoldDesk.

Every rule below was validated against the actual text of the two handbooks.
Two rules were deliberately NOT written:

  * De-hyphenation (`word-\\n word` -> `wordword`). There are exactly two
    line-end hyphens in 96 pages: "137-A" and "11-12". Both are identifiers.
    The standard rule would corrupt them and fix nothing.

  * Reading-order repair. 16 college pages carry headings hoisted out of
    reading order; 29 pages end mid-sentence. Neither is recoverable from a
    flat text stream. This script REPORTS them and stops. Fixing them needs
    per-character (x, y) coordinates from pdfplumber.

Usage:
    python normalize.py raw/college  --doc college --out clean/college
    python normalize.py raw/shs      --doc shs     --out clean/shs
"""

import argparse
import json
import pathlib
import re
import sys
import unicodedata
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "pipeline_config.json"

# --------------------------------------------------------------------------
# Per-document configuration lives in pipeline_config.json (shared with
# chunker.py and ingest.py) so it can be edited from the app without touching
# this file. The two handbooks are structurally different documents; one
# config would be wrong for both.
# --------------------------------------------------------------------------


def load_config(doc, config_path=DEFAULT_CONFIG):
    all_cfg = json.loads(pathlib.Path(config_path).read_text(encoding="utf-8"))
    docs = all_cfg["docs"]
    if doc not in docs:
        raise SystemExit(f"unknown doc {doc!r} in {config_path}; choices: {sorted(docs)}")
    d = docs[doc]
    sub = d["normalize"]
    return {
        "source": d["source"],
        "audience": d["audience"],
        "page_label_offset": d["page_label_offset"],
        "suppress_pages": d["suppress_pages"],
        "strip_lines": [re.compile(p) for p in sub["strip_line_patterns"]],
        "typos": {int(page): [tuple(pair) for pair in fixes]
                  for page, fixes in sub["typos"].items()},
    }

# Words that cannot end a sentence. Used only to rejoin a line that the
# extractor hard-broke mid-clause without leaving a trailing space.
DANGLING = set(
    "a an the at of in on and or for to with by from as is are was were "
    "that this his her their its shall must may can be been".split()
)

# Every bullet glyph these two handbooks use. U+F0A7 and U+F0B7 are Private Use
# Area codepoints -- Wingdings and Symbol font bullets that carry no Unicode
# meaning. Twelve of them in the SHS handbook. Untranslated they embed as noise.
BULLETS = {
    "\u2022": "- ",     # •
    "\uf0b7": "- ",     # Symbol font bullet
    "\uf0a7": "- ",     # Wingdings square bullet
    "\u00bb\u00bb": "  - ",   # »» nested marker (college p.7)
    "\u00bb": "- ",     # »
}

# A raw line beginning with any of these starts a new list item. Reflow must
# never absorb it into the previous line. Deciding this at the RAW line start is
# safe; a post-hoc regex cannot distinguish list item "8." from "Rule VI.
# Section 8." Thirty-three items were swallowed before this rule existed.
LIST_START = re.compile(
    r"^\s*(?:[\u2022\uf0a7\uf0b7\u00bb]|\(cid:\d+\)|\d{1,2}\.\s|[a-z]\.\s|\([a-z0-9]{1,3}\)\s)"
)

# pdfplumber emits "(cid:127)" for a glyph whose font has no ToUnicode map.
# At a line start it is almost always a decorative bullet. Anywhere else it is
# lost text and you need to look at the page image. Never delete it silently.
CID = re.compile(r"\(cid:\d+\)")

# Straight-ASCII equivalents. Applied AFTER reflow so that ’ and ” do not
# interfere with line-ending detection. The motto's own value is wrapped in
# curly quotes; v1's BM25 tokenizer (text.lower().split()) therefore produced
# the token '“excellence'. This is the rule that fixes that.
PUNCT = {
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-",
    "\u2011": "-",              # non-breaking hyphen
    "\u00a0": " ",              # non-breaking space
    "\u0002": "-",              # broken hyphen glyph: pro-rated, in-demand, ...
    "\u2028": "",               # LINE SEPARATOR, invisible: "and/<U+2028>or"
}

# NOT normalized: 'ú' in "Mapúa". It is the institution's name and belongs in
# chunk_text, in citations, and on screen. Students will type "Mapua" -- fold
# accents in the BM25 tokenizer at index and query time, not in the corpus.

TERMINAL = '.:;!?"'


def reflow(text, counters):
    """
    Join soft-wrapped lines into paragraphs.

    Signal: a line ending in a space is a soft wrap (the sentence continues).
    A line with no trailing space is a hard break. This holds for ~1577 of the
    3297 lines in these handbooks. The DANGLING backstop catches the rest.
    """
    lines = text.replace("\r", "").split("\n")
    out, buf = [], ""

    for i, line in enumerate(lines):
        join = line.endswith(" ")
        nxt_raw = lines[i + 1] if i + 1 < len(lines) else ""

        # A list item always starts its own line, whatever the previous line
        # ended with. This overrides both the soft-wrap and dangling rules.
        if LIST_START.match(nxt_raw):
            if join:
                counters["listbreak_forced"] += 1
            join = False

        elif not join and line.strip():
            words = line.strip().split()
            last = re.sub(r"[^\w]", "", words[-1]).lower() if words else ""
            nxt = nxt_raw.strip()
            if last in DANGLING and nxt and nxt[0].islower():
                join = True
                counters["reflow_dangling"] += 1
                line += " "

        if join:
            buf += line
            counters["reflow_softwrap"] += 1
        else:
            out.append(buf + line)
            buf = ""

    if buf:
        out.append(buf)
    return [l for l in out if l.strip()]


def clean_line(line, counters):
    for src, dst in PUNCT.items():
        if src in line:
            counters[f"punct_{unicodedata.name(src, hex(ord(src)))}"] += line.count(src)
            line = line.replace(src, dst)

    # Bullets, longest marker first so »» is not eaten by ».
    stripped = line.lstrip()
    m = CID.match(stripped)
    if m:
        line = "- " + stripped[m.end():].lstrip()
        counters["bullet_cid"] += 1
    else:
        for src in sorted(BULLETS, key=len, reverse=True):
            if stripped.startswith(src):
                line = BULLETS[src] + stripped[len(src):].lstrip()
                counters["bullet"] += 1
                break
        else:
            # A sub-bullet in the table of contents: "o Scholarship Offerings"
            line, n = re.subn(r"^\s*o\s+(?=[A-Z])", "  - ", line)
            counters["subbullet"] += n

    # A (cid:N) anywhere but the line start is lost text, not decoration.
    if CID.search(line):
        counters["WARN_cid_midline"] += 1

    # Any bullet glyph still sitting mid-line means reflow swallowed a list
    # item. That is a bug, not something to paper over.
    for src in BULLETS:
        if src in line.lstrip()[1:]:
            counters["WARN_swallowed_bullet"] += 1

    line, n = re.subn(r"\s+([.,;:])", r"\1", line)      # "known for ." -> "known for."
    counters["space_before_punct"] += n

    line = re.sub(r" {2,}", " ", line).rstrip()
    return line


def normalize_page(raw, page_no, cfg, counters):
    lines = reflow(raw, counters)

    kept = []
    for line in lines:
        if any(p.match(line.strip()) for p in cfg["strip_lines"]):
            counters["stripped_header"] += 1
            continue
        kept.append(clean_line(line, counters))

    text = "\n".join(l for l in kept if l)

    for wrong, right in cfg["typos"].get(page_no, []):
        if wrong in text:
            text = text.replace(wrong, right)
            counters["typo"] += 1
        else:
            print(f"  WARNING p{page_no}: typo fix {wrong!r} matched nothing", file=sys.stderr)

    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir", help="directory of 1.txt .. N.txt from the extractor")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=DEFAULT_CONFIG,
                     help="pipeline_config.json path (default: alongside this script)")
    args = ap.parse_args()

    cfg = load_config(args.doc, args.config)
    indir, outdir = pathlib.Path(args.indir), pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    counters = Counter()
    needs_coords = []          # pages whose reading order we cannot trust
    pages = sorted(indir.glob("*.txt"), key=lambda p: int(p.stem))

    for path in pages:
        n = int(path.stem)
        if n in cfg["suppress_pages"]:
            counters["suppressed_page"] += 1
            continue

        text = normalize_page(path.read_text(encoding="utf-8"), n, cfg, counters)
        if not text.strip():
            counters["empty_after_clean"] += 1
            continue

        (outdir / f"{n}.txt").write_text(text + "\n", encoding="utf-8")

        last = text.strip().split("\n")[-1]
        if last and last[-1] not in TERMINAL and len(last.split()) > 4:
            needs_coords.append(n)

    report = {
        "doc": args.doc,
        "source": cfg["source"],
        "audience": cfg["audience"],
        "page_label_offset": cfg["page_label_offset"],
        "pages_in": len(pages),
        "pages_out": len(pages) - counters["suppressed_page"] - counters["empty_after_clean"],
        "edits": dict(counters),
        "pages_ending_mid_sentence": needs_coords,
    }
    (outdir / "_normalize_report.json").write_text(json.dumps(report, indent=2))

    print(f"\n{cfg['source']}: {report['pages_in']} pages in, {report['pages_out']} out")
    for k, v in sorted(counters.items(), key=lambda kv: -kv[1]):
        print(f"  {v:>6}  {k}")

    if needs_coords:
        print(
            f"\n  {len(needs_coords)} pages end mid-sentence: {needs_coords}\n"
            "  These sentences continue onto the next page, and on some pages a\n"
            "  section heading has been hoisted between them. Do NOT join them\n"
            "  blind. Resolve with pdfplumber (x, y) coordinates before chunking."
        )

    left = Counter(c for p in outdir.glob("*.txt")
                   for c in p.read_text(encoding="utf-8") if ord(c) > 127)
    if left:
        print(f"\n  non-ASCII characters remaining: {dict(left)}")
        print("  Confirm each is intentional (e.g. ½ in a photo-size spec).")


if __name__ == "__main__":
    main()
