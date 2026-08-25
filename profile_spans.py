#!/usr/bin/env python3
"""
profile_spans.py — AskWhiz v2, handbook corpus, stage 3.5.

A font-size histogram is not enough to find headings. In the College handbook
12.0pt is body text and 13.5pt is *table* text set in Calibri, whose small
x-height makes it look the same size on the page. A "bigger than body" rule
would label 7,368 characters of table content as headings.

This script cross-tabulates size x font x weight and shows real example lines
from each bucket, so you can see what each one actually is before writing a
single line of the chunker.

    python profile_spans.py raw/college
    python profile_spans.py raw/shs
"""

import argparse
import collections
import json
import pathlib


def load(indir):
    spans = []
    for p in sorted(pathlib.Path(indir).glob("*.spans.json"), key=lambda p: int(p.stem.split(".")[0])):
        page = int(p.stem.split(".")[0])
        for s in json.loads(p.read_text(encoding="utf-8")):
            s["page"] = page
            spans.append(s)
    return spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir")
    ap.add_argument("--samples", type=int, default=2)
    args = ap.parse_args()

    spans = load(args.indir)
    if not spans:
        raise SystemExit(f"no *.spans.json in {args.indir} -- run extract.py first")

    # Round to 1dp: extract.py's 2dp rounding splits 12.0 and 12.02 into two
    # buckets for what is obviously the same typeface at the same size.
    for s in spans:
        s["size"] = round(s["size"], 1) if s["size"] else None

    body = collections.Counter(
        (s["size"] for s in spans if s["size"])
    ).most_common(1)[0][0]

    # Left margin: the most common x0 across all lines.
    margin = collections.Counter(round(s["x0"]) for s in spans).most_common(1)[0][0]

    buckets = collections.defaultdict(list)
    for s in spans:
        buckets[(s["size"], s["font"])].append(s)

    print(f"\n{args.indir}: {len(spans)} lines, body size {body}pt, left margin x0={margin}\n")
    print(f"{'size':>6} {'font':<26} {'lines':>6} {'chars':>7} {'x0':>6}  examples")
    print("-" * 110)

    for (size, font), group in sorted(buckets.items(), key=lambda kv: -sum(len(s["text"]) for s in kv[1])):
        chars = sum(len(s["text"]) for s in group)
        if chars < 40:
            continue
        x0s = collections.Counter(round(s["x0"]) for s in group).most_common(1)[0][0]
        ex = " | ".join(repr(s["text"][:40]) for s in group[: args.samples])
        print(f"{size:>6} {str(font):<26} {len(group):>6} {chars:>7} {x0s:>6}  {ex}")

    # ---- what would each candidate heading rule actually select? ----
    print("\n\nHEADING RULE CANDIDATES\n" + "-" * 60)

    def report(name, pred):
        hits = [s for s in spans if pred(s)]
        print(f"\n{name}: {len(hits)} lines")
        for s in hits[:8]:
            print(f"    p{s['page']:>3} {s['size']}pt {'bold' if s['bold'] else '    '} "
                  f"x0={s['x0']:>5}  {s['text'][:60]!r}")
        if len(hits) > 8:
            print(f"    ... and {len(hits) - 8} more")

    report("A. size > body           (what I proposed -- probably wrong)",
           lambda s: s["size"] and s["size"] > body + 0.5)

    report("B. bold AND at left margin",
           lambda s: s["bold"] and abs(s["x0"] - margin) < 3)

    report("C. bold AND at left margin AND <= 12 words",
           lambda s: s["bold"] and abs(s["x0"] - margin) < 3 and len(s["text"].split()) <= 12)

    report("D. ALL CAPS (what v1 effectively did)",
           lambda s: s["text"].strip() and
                     [c for c in s["text"] if c.isalpha()] and
                     all(c.isupper() for c in s["text"] if c.isalpha()) and
                     3 <= len(s["text"].strip()) <= 60)

    print("\n\nSANITY CHECKS -- these must land in the right bucket")
    print("-" * 60)
    for needle in ["SCHOOL MOTTO", "SUMMA CUM LAUDE", "LIST.", "TUITION FEES",
                   "Excellence and Relevance", "MCM COLLEGE STUDENT MANUAL"]:
        for s in spans:
            if needle in s["text"]:
                print(f"  {needle:<28} p{s['page']:>3}  {s['size']}pt  "
                      f"{'BOLD' if s['bold'] else 'reg '}  x0={s['x0']:>6}  {str(s['font'])[:22]}")
                break
        else:
            print(f"  {needle:<28} not found")


if __name__ == "__main__":
    main()
