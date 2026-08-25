#!/usr/bin/env python3
"""
extract.py — AskWhiz v2, handbook corpus, stage 1 of 4.

    EXTRACT  ->  normalize.py  ->  section chunker  ->  freeze to JSONL

Reads a handbook PDF and writes, per page:

    <out>/N.txt          plain text, one line per PDF line
    <out>/N.spans.json   every line's font size, font name, x0, top

Two things this does that a bare `page.extract_text()` does not:

1. SOFT-WRAP MARKING. normalize.py distinguishes a wrapped line (sentence
   continues) from a paragraph break by looking for a trailing space. A raw
   pdfplumber dump has no such marker. So we derive it from geometry: a line
   whose right edge reaches the text block's right margin was wrapped by the
   typesetter; a line that stops short ended a paragraph. We append a trailing
   space to the former. The signal comes from the page, not from luck.

2. SPANS. Heading detection needs font size, weight, and x/y position -- the
   information a flat text stream throws away. Without it you cannot tell the
   heading "SCHOOL MOTTO" from the wrapped sentence fragment "LIST." or the
   table row "SUMMA CUM LAUDE 1.00 <= GWA <= 1.25". The chunker reads
   N.spans.json, not N.txt.

Filenames are irrelevant -- pass whatever path you have.

    pip install pdfplumber
    python extract.py MCM-HANDBOOK-2020-COLLEGE.pdf --out raw/college
    python extract.py MCM-HANDBOOK-2020-SHS.pdf     --out raw/shs
"""

import argparse
import collections
import json
import pathlib
import statistics

import pdfplumber

# A line is treated as wrapped if its right edge lands within this many points
# of the widest right edge on the page. Justified text hits the margin exactly;
# ragged-right text lands a few points short. 4pt is under one character width
# at 11pt, so it will not swallow a genuinely short final line.
WRAP_TOLERANCE_PT = 4.0

# Font weight, not literal "bold" in the name. A naive "bold" substring check
# breaks in both directions on a variable-weight family: the College/SHS
# handbooks use classic Regular/Bold naming (ArialMT, Arial-BoldMT) where the
# substring check happens to work, but a family like Montserrat names its
# heading weight "-Black" (no "bold" substring -- false NEGATIVE, headings
# vanish) and its body weight "-SemiBold" (contains "bold" -- false POSITIVE,
# every paragraph reads as bold). SemiBold/DemiBold are excluded explicitly
# because they are body-text weights that happen to contain the substring.
def is_bold_weight(fontname):
    if not fontname:
        return False
    name = fontname.lower()
    if "semibold" in name or "demibold" in name or "demi-bold" in name:
        return False
    return any(k in name for k in ("bold", "black", "heavy", "extrabold", "ultrabold"))


# pdfplumber's default table finder fires on ANY line-like edges -- including
# the border of a full-bleed colored background banner, common on modern,
# heavily-designed handbooks. A "table" whose bbox covers nearly the whole
# page is that, not a real data table: a genuine table never spans edge to
# edge past the page margins. Left undetected, this poisons heading
# detection (in_table excludes every heading on the page) and the app's
# table/diagram review checks (a bogus table masks a real missing one).
TABLE_PAGE_COVERAGE = 0.9


def real_tables(page):
    """page.find_tables(), minus whole-page false positives."""
    out = []
    for t in page.find_tables():
        x0, top, x1, bottom = t.bbox
        if (x1 - x0) >= TABLE_PAGE_COVERAGE * page.width and \
                (bottom - top) >= TABLE_PAGE_COVERAGE * page.height:
            continue
        out.append(t)
    return out


def page_right_margin(lines):
    """
    The right edge of the text block.

    NOT the mode of the right edges: on a typical page almost every line ends at
    a different x1, so the mode is decided by a two-way tie broken on insertion
    order -- which on the test fixture returned the running header's edge (207pt)
    instead of the body margin (534pt), marking every line as wrapped.

    Justified body text reaches the margin exactly. Headers, page numbers, and
    centred tables all stop short of it. So the widest multi-word line wins.
    """
    wide = [l["x1"] for l in lines if len(l["text"].split()) >= 6]
    if not wide:
        wide = [l["x1"] for l in lines]
    return max(wide) if wide else None


def extract_page(page):
    # use_text_flow=False sorts by position, not by the order glyphs happen to
    # appear in the content stream. This is what fixes hoisted headings.
    lines = page.extract_text_lines(strip=True, return_chars=True, use_text_flow=False)
    lines.sort(key=lambda l: (round(l["top"]), l["x0"]))

    # Table bounding boxes. A bold, left-margin, short line INSIDE one of these
    # is a table header row -- e.g. "Grade Baccalaureate Programs Diploma
    # Programs" -- not a section heading. Without this the grading table becomes
    # a section and its rows become that section's body text.
    tboxes = [t.bbox for t in real_tables(page)]

    def in_table(ln):
        return any(
            x0 - 2 <= ln["x0"] and ln["x1"] <= x1 + 2 and top - 2 <= ln["top"] and ln["bottom"] <= bot + 2
            for x0, top, x1, bot in tboxes
        )

    margin = page_right_margin(lines)
    out_text, out_spans = [], []

    for ln in lines:
        chars = ln.get("chars") or []
        sizes = [c["size"] for c in chars]
        fonts = collections.Counter(c["fontname"] for c in chars)
        font = fonts.most_common(1)[0][0] if fonts else None

        wrapped = margin is not None and ln["x1"] >= margin - WRAP_TOLERANCE_PT
        out_text.append(ln["text"] + (" " if wrapped else ""))

        out_spans.append({
            "text": ln["text"],
            # 1dp: 2dp splits one typeface into 12.0 and 12.02 buckets.
            "size": round(statistics.median(sizes), 1) if sizes else None,
            "font": font,
            "bold": is_bold_weight(font),
            "italic": bool(font) and "italic" in font.lower(),
            "x0": round(ln["x0"], 1),
            "top": round(ln["top"], 1),
            "wrapped": wrapped,
            "in_table": in_table(ln),
        })

    return "\n".join(out_text), out_spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    sizes = collections.Counter()
    fonts = collections.Counter()
    wraps = blank = 0
    cid_pages = collections.Counter()

    with pdfplumber.open(args.pdf) as pdf:
        for page in pdf.pages:
            text, spans = extract_page(page)
            n = page.page_number

            (outdir / f"{n}.txt").write_text(text + "\n", encoding="utf-8")
            (outdir / f"{n}.spans.json").write_text(json.dumps(spans, indent=1), encoding="utf-8")

            if not text.strip():
                blank += 1
            c = text.count("(cid:")
            if c:
                cid_pages[n] = c
            for s in spans:
                if s["size"]:
                    sizes[s["size"]] += len(s["text"])
                if s["font"]:
                    fonts[s["font"]] += len(s["text"])
                wraps += s["wrapped"]

    total = len(list(outdir.glob("*.txt")))
    print(f"\n{args.pdf}: {total} pages -> {outdir}/")
    print(f"  {wraps} lines marked as soft-wrapped, {blank} pages with no text\n")

    print("FONTS (by character count)")
    for f, n in fonts.most_common():
        print(f"  {n:>7}  {f}")

    print("\nFONT SIZES (by character count)")
    body = sizes.most_common(1)[0][0] if sizes else None
    for s, n in sorted(sizes.items(), reverse=True):
        tag = "  <-- body text" if s == body else ("  heading candidate" if body and s > body + 0.5 else "")
        print(f"  {n:>7}  {s:>6} pt{tag}")

    if body:
        print(f"\n  body size = {body} pt -> spans above {body + 0.5} pt are heading candidates.")

    if cid_pages:
        print(f"\n  UNMAPPED GLYPHS: {sum(cid_pages.values())} '(cid:N)' tokens on "
              f"{len(cid_pages)} pages: {sorted(cid_pages)}")
        print("  A font on these pages has no ToUnicode map. Its characters render on")
        print("  screen but carry no recoverable text. Usually decorative bullets --")
        print("  but if a TABLE sits on one of these pages, this is why it extracted")
        print("  as nothing. Rasterize and read those pages before trusting them.")

    if blank:
        print(f"\n  {blank} pages produced no text at all. Check them against the page images.")

    if wraps == 0:
        print("\n  WARNING: no soft-wraps detected. normalize.py's reflow will do nothing.")
        print("  The right-margin heuristic failed -- check WRAP_TOLERANCE_PT.")


if __name__ == "__main__":
    main()
