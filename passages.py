"""
passages.py - split each frozen chunk into small, labelled search passages.

WHY THIS EXISTS (the motto bug)
    v1 could not answer "What is the school motto?". The answer is three words,
    "Excellence and Relevance", and in our frozen corpus it sits at the bottom of
    a 155-token chunk whose title is MISSION. Embed that whole chunk as one
    vector and the motto is ~2% of its meaning: the vector is "about the
    mission", so a motto question lands elsewhere.

    The fix is "small to search, big to answer":
      - SEARCH over small passages, each carrying its full label path:
            [College Student Handbook > MISSION > SCHOOL MOTTO]
            "Excellence and Relevance"
        A motto question now matches a passage that is ABOUT the motto.
      - ANSWER from the whole parent chunk, so the AI still sees the context
        and the citation is the real handbook section and page.

    The frozen chunks are not changed. Passages are derived from them at index
    build time, so this is one change to search, measured on its own (rule 1).
"""

import re

MAX_WORDS = 120      # target passage size
SPLIT_OVER = 180     # a single block longer than this is windowed
OVERLAP = 30         # words shared between windows of one long block

_BULLET = re.compile(r"^\s*([-*•]|\(?[0-9ivxIVX]+[.)]|\(?[a-zA-Z][.)])\s+")


def _is_heading(line: str) -> bool:
    """A sub-heading inside a chunk: short, no sentence ending, starts uppercase,
    and either ALL CAPS or at most five words ('The School', 'CORE VALUES')."""
    t = line.strip().strip('"')
    if not t or _BULLET.match(line):
        return False
    words = t.split()
    if len(words) > 8 or t[-1] in ".,;!?":
        return False
    if not t[0].isupper():
        return False
    letters = [c for c in t if c.isalpha()]
    caps = letters and sum(c.isupper() for c in letters) / len(letters) > 0.7
    return bool(caps or len(words) <= 5)


def _windows(words, size=MAX_WORDS, overlap=OVERLAP):
    step = size - overlap
    for start in range(0, max(len(words) - overlap, 1), step):
        yield words[start:start + size]


def _strip_breadcrumb(text: str, breadcrumb: str) -> str:
    lines = text.split("\n")
    if lines and lines[0].strip() == breadcrumb.strip():
        lines = lines[1:]
    return "\n".join(lines).strip()


def _path(breadcrumb: str, heading: str | None) -> str:
    """Append the in-chunk heading to the chunk breadcrumb, unless it is
    already the last label (avoids '> MISSION > MISSION')."""
    if not heading:
        return breadcrumb
    inner = breadcrumb.strip("[]")
    last = inner.split(" > ")[-1].strip()
    if last.lower() == heading.lower():
        return breadcrumb
    return f"[{inner} > {heading}]"


def split_chunk(chunk: dict) -> list[dict]:
    """Return the passages for one chunk. Every chunk yields at least one."""
    crumb = chunk["breadcrumb"]
    body = _strip_breadcrumb(chunk["chunk_text"], crumb)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]

    # 1. walk the blocks, tracking the current sub-heading
    pieces = []          # (heading, text)
    heading = None
    headings_seen = []
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip()]
        if len(lines) == 1 and _is_heading(lines[0]):
            heading = lines[0].strip()
            headings_seen.append(heading)
            continue
        if len(lines) > 1 and _is_heading(lines[0]):
            heading = lines[0].strip()
            headings_seen.append(heading)
            block = "\n".join(lines[1:])
        pieces.append((heading, " ".join(block.split())))

    # a heading with nothing after it (rare) still deserves a passage
    if not pieces:
        pieces = [(heading, " ".join(body.split()) or crumb)]

    # 2. merge neighbours under the same heading up to MAX_WORDS;
    #    window anything too long
    merged = []
    for h, text in pieces:
        words = text.split()
        if len(words) > SPLIT_OVER:
            for w in _windows(words):
                merged.append([h, " ".join(w)])
            continue
        if merged and merged[-1][0] == h and \
                len(merged[-1][1].split()) + len(words) <= MAX_WORDS:
            merged[-1][1] += " " + text
        else:
            merged.append([h, text])

    out = []
    for i, (h, text) in enumerate(merged):
        path = _path(crumb, h)
        out.append({
            "passage_id": f"{chunk['chunk_id']}#p{i}",
            "parent_id": chunk["chunk_id"],
            "audience": chunk["audience"],
            "path": path,
            "text": text,
            # what gets embedded and keyword-indexed: label path + words
            "index_text": f"{path}\n{text}",
        })

    # 3. one 'outline' passage per chunk: its labels and sub-headings only.
    #    Helps broad questions ("what student services are there?") find the
    #    section even when no single paragraph says it.
    if headings_seen:
        outline = f"{crumb}\nCovers: " + "; ".join(dict.fromkeys(headings_seen))
        out.append({
            "passage_id": f"{chunk['chunk_id']}#outline",
            "parent_id": chunk["chunk_id"],
            "audience": chunk["audience"],
            "path": crumb,
            "text": outline,
            "index_text": outline,
        })
    return out
