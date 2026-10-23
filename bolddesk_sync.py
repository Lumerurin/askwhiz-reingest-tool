#!/usr/bin/env python3
"""
bolddesk_sync.py - keep AskWhiz's copy of the BoldDesk help center up to date.

    python bolddesk_sync.py            # check BoldDesk, update what changed
    python bolddesk_sync.py --full     # re-download every article

BoldDesk is the source of truth. This script makes our copy match it:

    1. Lists all knowledge-base articles (one cheap request per 100 articles).
    2. Keeps only articles that are Published + Active + visible to Any Users.
       Drafts, staff-only and group-only articles are never downloaded.
    3. Downloads the PUBLISHED text of articles that are new or changed since
       the last run (compares each article's last-modified and published
       dates). Unchanged articles are reused from bolddesk_raw/.
    4. Forgets articles that were unpublished, hidden or deleted.
    5. Writes chunks/bolddesk_kb.chunks.jsonl in the same format as the
       handbook chunks, so the Search Index tab and Gate 4 treat it the same.

Safety
    - Read-only. The only BoldDesk addresses used are
      GET /api/v1/kb/articles and GET /api/v1/kb/article/{id}/published.
      Never tickets, never contacts.
    - If BoldDesk can't be reached, nothing is changed.
    - If the number of usable articles suddenly drops by more than half (for
      example the key lost its permission), it refuses to wipe the copy and
      asks a person to check. Use --allow-shrink once you've confirmed it.
    - Article text written by staff is treated as content to answer from,
      never as instructions to the chatbot (the answering engine enforces this).

Needs .env in this folder (never commit it):
    BOLDDESK_DOMAIN=support.mcm.edu.ph
    BOLDDESK_API_KEY=...

The chatbot server will run sync() on a timer (part 2); this command line is
the same code, for running and checking it by hand.
"""

import argparse
import datetime
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "bolddesk_raw"                     # cache, not committed
CHUNKS_FILE = HERE / "chunks" / "bolddesk_kb.chunks.jsonl"
# Label at the front of every article's breadcrumb. Deliberately NOT "MMCM Help
# Center": the word MMCM is rare in the handbooks (they say MCM), so putting it
# on every article made them match any question mentioning MMCM (measured:
# "What is MMCM's mission?" fell from #1 to #3).
SOURCE = "Help Center"
AUDIENCE = "all"          # shown to every student group (see retrieve.py)
DOC_ID = "bolddesk_kb"
PER_PAGE = 100
SPLIT_ARTICLE_OVER = 600  # words; longer articles are split at their headings...
CHUNK_TARGET = 400        # ...and neighbouring pieces re-joined up to this size
PAUSE = 0.25              # seconds between article downloads (rate limits)
# Bump when html_to_text changes, so cached articles are re-downloaded and
# re-converted on the next sync without anyone passing --full.
FORMAT_VERSION = 2


# ------------------------------------------------------------------ client ---

class BoldDeskError(Exception):
    pass


class Client:
    def __init__(self, domain, key):
        self.domain = domain.replace("https://", "").replace("http://", "").strip("/")
        self.key = key

    @classmethod
    def from_env(cls, folder=HERE):
        env = {}
        f = Path(folder) / ".env"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
        import os
        domain = os.environ.get("BOLDDESK_DOMAIN") or env.get("BOLDDESK_DOMAIN")
        key = os.environ.get("BOLDDESK_API_KEY") or env.get("BOLDDESK_API_KEY")
        if not domain or not key:
            raise BoldDeskError("BOLDDESK_DOMAIN and BOLDDESK_API_KEY are not set (.env or environment)")
        return cls(domain, key)

    def get(self, path, params=None):
        url = f"https://{self.domain}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"x-api-key": self.key,
                                                   "accept": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(30 * (attempt + 1))
                    continue
                reason = {401: "API key not accepted", 403: "no Knowledge Base permission",
                          404: "not found"}.get(e.code, "")
                raise BoldDeskError(f"{path}: HTTP {e.code} {reason}".strip())
            except (urllib.error.URLError, TimeoutError) as e:
                raise BoldDeskError(f"could not reach {self.domain}: {getattr(e, 'reason', e)}")
        raise BoldDeskError("rate limited by BoldDesk; try again later")


# ---------------------------------------------------------- html -> text ---

class _TextBuilder(HTMLParser):
    """Turns article HTML into plain text that keeps its structure:
    headings become their own lines, list items become '- ' lines, table rows
    become 'a | b | c', and paragraphs are separated by blank lines (which is
    how passages.py finds sub-headings and blocks)."""

    BLOCK = {"p", "div", "section", "article", "blockquote", "pre", "ul", "ol",
             "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}
    HEAD = {"h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.cells, self.in_cell = [], 0, None, False
        self.href = None
        self.headings = []
        self._head_buf = None

    def _nl(self, n=2):
        text = "".join(self.out)
        stripped = text.rstrip(" ")
        have = len(stripped) - len(stripped.rstrip("\n"))
        self.out = [stripped + "\n" * max(0, n - have)] if text else []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.HEAD:
            self._nl(2)
            self._head_buf = []
        elif tag == "li":
            self._nl(1)
            self.out.append("- ")
        elif tag == "br":
            self._nl(1)
        elif tag == "tr":
            self._nl(1)
            self.cells = []
        elif tag in ("td", "th"):
            self.in_cell = True
            self.cells is not None and self.cells.append("")
        elif tag == "a":
            self.href = a.get("href")
        elif tag == "img" and _useful_alt(a.get("alt")):
            self._emit(f"[image: {a['alt']}]")
        elif tag in self.BLOCK:
            self._nl(2)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.HEAD:
            title = " ".join("".join(self._head_buf or []).split())
            self._head_buf = None
            if title:
                self.headings.append(title)
                self.out.append(title)
            self._nl(2)
        elif tag in ("td", "th"):
            self.in_cell = False
        elif tag == "tr" and self.cells is not None:
            row = " | ".join(c.strip() for c in self.cells if c.strip())
            if row:
                self.out.append(row)
            self.cells = None
            self._nl(1)
        elif tag == "a":
            self.href = None
        elif tag in self.BLOCK:
            self._nl(2)

    def _emit(self, text):
        if self._head_buf is not None:
            self._head_buf.append(text)
        elif self.in_cell and self.cells:
            self.cells[-1] += text
        else:
            self.out.append(text)

    def handle_data(self, data):
        if self.skip:
            return
        data = re.sub(r"\s+", " ", data)
        if not data.strip() and not self.out:
            return
        href = self.href
        if href and href.startswith("http") and data.strip() and data.strip() not in href:
            data = f"{data} ({href})"
            self.href = None          # print each link once
        self._emit(data)


_FILE_LIKE = re.compile(r"^[\w\s.-]*\.(jpe?g|png|gif|webp|svg|bmp)$|^(image|img|media|screenshot)[\s_-]*\d*$", re.I)


def _useful_alt(alt) -> bool:
    """Alt text worth keeping: real words, not a file name like Media_6.jpg."""
    alt = (alt or "").strip()
    return len(alt) > 3 and not _FILE_LIKE.match(alt)


def html_to_text(raw_html: str) -> str:
    p = _TextBuilder()
    p.feed(raw_html or "")
    p.close()
    text = html.unescape("".join(p.out))
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- chunking ---

def _usable(a: dict) -> bool:
    status = a.get("status") or {}
    name = (status.get("name") if isinstance(status, dict) else str(status)) or ""
    return (a.get("visibilityOptionId") == 1
            and name.lower().startswith("publish")
            and a.get("stateId") == 1)


def _name(x):
    return (x or {}).get("name") if isinstance(x, dict) else x


def article_chunks(art: dict, domain: str) -> list[dict]:
    """One chunk per article; very long articles split at their headings."""
    category = _name(art.get("category")) or "General"
    section = _name(art.get("section"))
    title = " ".join((art.get("title") or f"Article {art['id']}").split())
    labels = [SOURCE, category] + ([section] if section else []) + [title]
    crumb = "[" + " > ".join(labels) + "]"
    url = ((art.get("articleSeo") or {}).get("canonicalUrl")
           or f"https://{domain}/kb/article/{art['id']}")
    url = url.replace("https: //", "https://")
    body = art.get("text") or ""

    parts = [(None, body)]
    if len(body.split()) > SPLIT_ARTICLE_OVER and art.get("headings"):
        # 1. cut at headings
        pieces, current, buf = [], None, []
        heads = set(art["headings"])
        for block in body.split("\n\n"):
            if block.strip() in heads and buf:
                pieces.append((current, "\n\n".join(buf)))
                buf = []
            if block.strip() in heads:
                current = block.strip()
            buf.append(block)
        pieces.append((current, "\n\n".join(buf)))
        # some articles repeat a heading (heading line, then a table under the
        # same title): treat consecutive pieces with one heading as one piece
        joined = []
        for h, text in pieces:
            if joined and joined[-1][0] == h and h is not None:
                joined[-1] = (h, joined[-1][1] + "\n\n" + text)
            else:
                joined.append((h, text))
        pieces = joined
        # 2. re-join neighbours up to CHUNK_TARGET words, so a heading is never
        #    left on its own and the AI gets readable, self-contained sections
        groups = []
        for h, text in pieces:
            n = len(text.split())
            # tiny pieces (a lone "Contact Us") always join the previous one
            if groups and (groups[-1]["words"] + n <= CHUNK_TARGET or n < 60):
                groups[-1]["heads"].append(h)
                groups[-1]["texts"].append(text)
                groups[-1]["words"] += n
            else:
                groups.append({"heads": [h], "texts": [text], "words": n})
        parts = []
        for g in groups:
            # label the chunk with its heading only if it is all one section
            uniq = set(g["heads"])
            parts.append((uniq.pop() if len(uniq) == 1 else None, "\n\n".join(g["texts"])))

    chunks = []
    for i, (heading, text) in enumerate(parts):
        bc = crumb if heading is None or len(parts) == 1 else crumb[:-1] + f" > {heading}]"
        cid = f"{DOC_ID}_a{art['id']}" + (f"_{i}" if len(parts) > 1 else "")
        chunks.append({
            "chunk_id": cid,
            "source": SOURCE,
            "document_type": "kb_article",
            "source_type": "kb_article",
            "audience": AUDIENCE,
            "title": title,
            "breadcrumb": bc,
            "pages": [],
            "page_labels": [],
            "source_url": url,
            "article_id": art["id"],
            "published_on": art.get("publishedOn"),
            "last_modified_on": art.get("lastModifiedOn"),
            "tags": [t.get("name") for t in (art.get("tag") or []) if isinstance(t, dict)],
            "token_count": len(text.split()),
            "chunk_text": f"{bc}\n{text}".strip(),
        })
    return chunks


# -------------------------------------------------------------------- sync ---

def _cache_path(article_id):
    return RAW_DIR / f"{article_id}.json"


def sync(client: Client, full=False, allow_shrink=False, log=print) -> dict:
    """Make chunks/bolddesk_kb.chunks.jsonl match BoldDesk. Returns a report.
    Raises BoldDeskError (and changes nothing) if BoldDesk can't be read."""
    listed, page = [], 1
    while True:
        data = client.get("/api/v1/kb/articles",
                          {"Page": page, "PerPage": PER_PAGE, "RequiresCounts": "true"})
        batch = data.get("result") or []
        listed += batch
        total = data.get("count")
        if len(batch) < PER_PAGE or (total is not None and len(listed) >= total):
            break
        page += 1

    usable = {a["id"]: a for a in listed if _usable(a)}
    RAW_DIR.mkdir(exist_ok=True)
    cached_ids = {int(p.stem) for p in RAW_DIR.glob("*.json") if p.stem.isdigit()}

    if cached_ids and len(usable) < len(cached_ids) / 2 and not allow_shrink:
        raise BoldDeskError(
            f"only {len(usable)} usable articles now, {len(cached_ids)} last time. "
            "Refusing to remove that many at once; check the API key's permissions "
            "and BoldDesk, then re-run with --allow-shrink if this is intended.")

    report = {"added": [], "updated": [], "unchanged": [], "removed": [], "empty": [],
              "listed": len(listed), "usable": len(usable)}
    articles = []
    for aid, meta in sorted(usable.items()):
        stamp = [meta.get("lastModifiedOn"), meta.get("publishedOn")]
        cp = _cache_path(aid)
        cached = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else None
        if cached and cached.get("stamp") == stamp and cached.get("fmt") == FORMAT_VERSION and not full:
            articles.append(cached["article"])
            report["unchanged"].append(cached["article"]["title"])
            continue
        res = client.get(f"/api/v1/kb/article/{aid}/published").get("result") or {}
        time.sleep(PAUSE)
        if res.get("isPublished") is False:
            continue
        raw = res.get("description") or ""
        text = html_to_text(raw) if "<" in raw else raw.strip()
        if not text and res.get("markdownDescription"):
            text = res["markdownDescription"].strip()
        heads = _TextBuilder()
        heads.feed(raw)
        art = {
            "id": aid,
            "title": res.get("title") or meta.get("title"),
            "category": res.get("category") or meta.get("category"),
            "section": res.get("section") or meta.get("section"),
            "articleSeo": res.get("articleSeo") or meta.get("articleSeo"),
            "publishedOn": res.get("publishedOn") or meta.get("publishedOn"),
            "lastModifiedOn": res.get("lastModifiedOn") or meta.get("lastModifiedOn"),
            "tag": res.get("tag") or [],
            "headings": heads.headings,
            "text": text,
        }
        if not text:
            report["empty"].append(art["title"])
        cp.write_text(json.dumps({"stamp": stamp, "fmt": FORMAT_VERSION, "article": art},
                                 ensure_ascii=False, indent=1),
                      encoding="utf-8")
        articles.append(art)
        if cached and cached.get("stamp") == stamp:
            report["unchanged"].append(art["title"])     # only re-converted
        else:
            report["updated" if cached else "added"].append(art["title"])

    for gone in cached_ids - set(usable):
        cp = _cache_path(gone)
        try:
            title = json.loads(cp.read_text(encoding="utf-8"))["article"]["title"]
        except Exception:
            title = f"article {gone}"
        cp.unlink(missing_ok=True)
        report["removed"].append(title)

    chunks = [c for a in articles if a.get("text") for c in article_chunks(a, client.domain)]
    CHUNKS_FILE.parent.mkdir(exist_ok=True)
    tmp = CHUNKS_FILE.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks),
                   encoding="utf-8")
    tmp.replace(CHUNKS_FILE)
    report.update(chunks=len(chunks), changed=bool(report["added"] or report["updated"]
                                                   or report["removed"]),
                  synced_at=datetime.datetime.now().isoformat(timespec="seconds"))
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="re-download every article")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="allow removing more than half the articles in one run")
    args = ap.parse_args()
    try:
        client = Client.from_env()
        print(f"Syncing the help center on {client.domain} (read-only) ...")
        r = sync(client, full=args.full, allow_shrink=args.allow_shrink)
    except BoldDeskError as e:
        sys.exit(f"! {e}\n  Nothing was changed.")
    print(f"\n{r['listed']} articles in BoldDesk, {r['usable']} usable "
          f"(Published + Active + Any Users) -> {r['chunks']} chunks")
    for key, label in (("added", "New"), ("updated", "Updated"), ("removed", "Removed"),
                       ("empty", "Empty (no text, skipped)")):
        if r[key]:
            print(f"\n{label} ({len(r[key])}):")
            for t in r[key]:
                print(f"  - {t}")
    print(f"\nUnchanged: {len(r['unchanged'])}")
    print(f"Wrote {CHUNKS_FILE.relative_to(HERE)}"
          + ("" if r["changed"] else " (no changes since last sync)"))


if __name__ == "__main__":
    main()
