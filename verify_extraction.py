#!/usr/bin/env python3
"""
verify_extraction.py — AskWhiz v2, Sprint 1, step 1.

Answers three questions about a handbook PDF:

  1. Does every page that PROMISES a table actually yield one?
  2. Are the fonts recoverable, or will text extraction silently drop glyphs?
  3. What font sizes distinguish headings from body text? (input to the chunker)

Run BEFORE writing the chunker. Re-run whenever a new handbook lands.

    pip install pdfplumber
    python verify_extraction.py MCMHANDBOOK2020COLLEGE.pdf
    python verify_extraction.py MCMHANDBOOK2020COLLEGE.pdf --pages 17,21,27

Exit code 1 if any dangling-table assertion fails. Wire that into CI.
"""

import argparse
import collections
import re
import sys

import pdfplumber

# Prose that promises a table. If a page says one of these and yields no table,
# the ingest is emitting a chunk that references content it does not contain.
TABLE_PROMISES = re.compile(
    r"table below|following table|table shows|shown in the table|as indicated in the table",
    re.IGNORECASE,
)


def scan_page(page):
    """Everything we want to know about one page, in one pass."""
    text = page.extract_text() or ""
    tables = page.extract_tables()

    # pdfplumber exposes per-character font metadata. If chars is empty but the
    # page clearly has ink, the text layer is broken (bad ToUnicode map) or the
    # content is a raster image.
    chars = page.chars
    sizes = collections.Counter(round(c["size"], 1) for c in chars)
    fonts = collections.Counter(c["fontname"] for c in chars)

    return {
        "page": page.page_number,
        "chars": len(chars),
        "words": len(text.split()),
        "images": [(round(im["width"]), round(im["height"])) for im in page.images],
        "tables": [(len(t), max((len(r) for r in t), default=0)) for t in tables],
        "promises_table": bool(TABLE_PROMISES.search(text)),
        "sizes": sizes,
        "fonts": fonts,
    }


def body_size(all_sizes):
    """The most common font size across the document is the body text size."""
    return all_sizes.most_common(1)[0][0] if all_sizes else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="comma-separated 1-based page numbers")
    args = ap.parse_args()

    wanted = None
    if args.pages:
        wanted = {int(p) for p in args.pages.split(",")}

    all_sizes = collections.Counter()
    all_fonts = collections.Counter()
    reports = []
    failures = []

    with pdfplumber.open(args.pdf) as pdf:
        total = len(pdf.pages)
        for page in pdf.pages:
            if wanted and page.page_number not in wanted:
                continue
            r = scan_page(page)
            all_sizes.update(r["sizes"])
            all_fonts.update(r["fonts"])
            reports.append(r)

            # ---- assertion 1: promised tables must exist ----
            if r["promises_table"] and not r["tables"]:
                failures.append(
                    f"page {r['page']}: prose promises a table, extract_tables() "
                    f"returned nothing. images on page: {r['images'] or 'none'}"
                )

            # ---- assertion 2: no page with ink should have zero chars ----
            if r["chars"] == 0 and not r["images"]:
                failures.append(f"page {r['page']}: no chars and no images — empty?")

    print(f"\n{args.pdf} — {total} pages, {len(reports)} scanned\n")

    # ---- fonts: is anything unrecoverable? ----
    print("FONTS (by character count)")
    for name, n in all_fonts.most_common():
        print(f"  {n:>7}  {name}")
    print()

    print("FONT SIZES (by character count)")
    body = body_size(all_sizes)
    for size, n in sorted(all_sizes.items(), reverse=True):
        tag = "  <-- body text" if size == body else ""
        heading = "  heading candidate" if body and size > body + 0.5 else ""
        print(f"  {n:>7}  {size:>5} pt{tag}{heading}")
    print(f"\n  body size = {body} pt")
    print(f"  => treat spans > {body + 0.5} pt as heading candidates,")
    print("     then confirm with bold fontname + left-margin x0 + sort by top.\n")

    # ---- tables ----
    print("TABLES")
    for r in reports:
        if r["tables"] or r["promises_table"]:
            shape = ", ".join(f"{rows}x{cols}" for rows, cols in r["tables"]) or "NONE"
            flag = " ** PROMISED **" if r["promises_table"] else ""
            print(f"  page {r['page']:>3}: {shape}{flag}")
    print()

    # ---- suspicious pages ----
    print("SUSPICIOUS PAGES (low chars, or images present)")
    for r in reports:
        if r["chars"] < 200 or r["images"]:
            print(
                f"  page {r['page']:>3}: {r['chars']:>5} chars, "
                f"{r['words']:>4} words, images={r['images'] or 'none'}"
            )
    print()

    if failures:
        print("FAILURES")
        for f in failures:
            print(f"  ! {f}")
        print(f"\n{len(failures)} failure(s). Do not ingest this corpus.\n")
        return 1

    print("All assertions passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
