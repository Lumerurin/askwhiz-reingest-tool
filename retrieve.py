"""
retrieve.py - hybrid search over the AskWhiz handbook index.

    from retrieve import Retriever
    r = Retriever("index")
    hits = r.search("What is the school motto?", audience="college", k=5)
    for h in hits: print(h["chunk_id"], h["breadcrumb"], h["page_labels"])

Each hit is a whole handbook chunk (the thing the AI answers from and the page
you cite), found through its best-matching small passage.

FIXES OVER THE THESIS VERSION (each was a confirmed v1 bug)
  1. Audience filter BEFORE scoring. College and SHS chunks never compete.
  2. Weighted Reciprocal Rank Fusion instead of min-max normalisation:
         score(p) = 0.3 / (60 + rank_dense(p)) + 0.7 / (60 + rank_keyword(p))
     v1 forced each list's top score to 1.0, so dense #1 could never lose.
  3. Wide candidate pool (50 per method), not top_k * 2.
  4. Keyword tokenizer keeps "1.25" and "137-a", strips punctuation, folds
     accents, so "absences." == "absences" and a trailing "?" matches.
  5. The label path is part of every passage's indexed text (the motto fix).
  6. Index loaded ONCE at startup, never re-parsed per question.
"""

import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np

# Meaning share 0.3 chosen by sweep on the 20 gold questions (Oct 21 2026):
# 0.0 -> MRR 0.779, 0.2/0.3 -> 0.808 (all 20 in top 3), 0.6 -> 0.742.
W_DENSE, W_SPARSE, RRF_K, POOL = 0.3, 0.7, 60, 50

STOP = set("""a an and are as at be by can do does for from has have how i if in
into is it its me my of on or our so than that the their them then there these
they this to was we what when where which who why will with you your""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-'][a-z0-9]+)*")

# Same thing, different words. Applied to questions AND passages, so either
# side's wording matches. Kept small and specific to MMCM's handbooks.
TERM_MAP = {
    # NOTE: "mmcm" -> "mcm" was tried and removed: "mcm" is in so many passages
    # that it added noise (gold MRR 0.779 -> 0.746, keyword-only, Oct 21 2026).
    "first": "one", "once": "one",
    "second": "two", "twice": "two",
    "third": "three", "thrice": "three",
    "fourth": "four", "fifth": "five",
}
# Spelled out for the meaning model, which reads whole phrases, not tokens.
QUERY_REWRITES = [
    (re.compile(r"\bM?MCM\b", re.I), "Malayan Colleges Mindanao (MCM)"),
]

USE_TERM_MAP = True        # keyword side: third -> three etc.
USE_QUERY_REWRITE = True   # meaning side: MMCM -> "Malayan Colleges Mindanao (MCM)"


def rewrite_query(q: str) -> str:
    if not USE_QUERY_REWRITE:
        return q
    for pattern, repl in QUERY_REWRITES:
        q = pattern.sub(repl, q)
    return q


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    out = []
    for t in _TOKEN.findall(text):
        if t.endswith("'s"):
            t = t[:-2]
        if t in STOP:
            continue
        # light plural folding: absences -> absence, fees -> fee (not 'class')
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss") and not t[-2].isdigit():
            t = t[:-1]
        if USE_TERM_MAP:
            t = TERM_MAP.get(t, t)
        out.append(t)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = np.array([len(d) for d in docs], dtype=float)
        self.avg = float(self.len.mean()) if len(docs) else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str], idx: list[int]) -> np.ndarray:
        out = np.zeros(len(idx))
        for j, i in enumerate(idx):
            tf, L = self.tf[i], self.len[i]
            s = 0.0
            for t in query:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
            out[j] = s
        return out


class Retriever:
    def __init__(self, index_dir="index", use_dense=True, w_dense=W_DENSE):
        self.w_dense, self.w_sparse = w_dense, 1.0 - w_dense
        d = Path(index_dir)
        self.manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        self.passages = json.loads((d / "passages.json").read_text(encoding="utf-8"))
        self.chunks = json.loads((d / "chunks.json").read_text(encoding="utf-8"))
        self.bm25 = BM25([tokenize(p["index_text"]) for p in self.passages])
        self.use_dense = use_dense and self.manifest.get("model") is not None
        self.vectors = None
        if self.use_dense:
            self.vectors = np.load(d / "vectors.npy")
            from fastembed import TextEmbedding
            self.embedder = TextEmbedding(self.manifest["model"])
            if self.vectors.shape[1] != self.manifest["dims"]:
                raise SystemExit("index vectors do not match manifest dims; rebuild the index")

    def _embed_query(self, q: str) -> np.ndarray:
        v = np.asarray(next(iter(self.embedder.query_embed([q]))), dtype=np.float32)
        return v / (np.linalg.norm(v) or 1.0)

    def search(self, question: str, audience: str | None = None, k: int = 5) -> list[dict]:
        idx = [i for i, p in enumerate(self.passages) if audience is None or p["audience"] == audience]
        if not idx:
            return []

        fused = np.zeros(len(idx))
        w_sparse = self.w_sparse if self.use_dense else 1.0
        # keyword ranking
        sp = self.bm25.scores(tokenize(question), idx)
        order = np.argsort(-sp)[:POOL]
        for r, j in enumerate(order):
            if sp[j] > 0:
                fused[j] += w_sparse / (RRF_K + r + 1)
        # meaning ranking
        if self.use_dense and self.w_dense > 0:
            dn = self.vectors[idx] @ self._embed_query(rewrite_query(question))
            for r, j in enumerate(np.argsort(-dn)[:POOL]):
                fused[j] += self.w_dense / (RRF_K + r + 1)

        # a chunk scores as its best passage
        best = {}
        for j in np.argsort(-fused):
            if fused[j] <= 0:
                break
            p = self.passages[idx[j]]
            if p["parent_id"] not in best:
                best[p["parent_id"]] = (float(fused[j]), p)
            if len(best) >= k:
                break

        hits = []
        for cid, (score, p) in best.items():
            c = self.chunks[cid]
            hits.append({
                "chunk_id": cid,
                "score": round(score, 5),
                "breadcrumb": c["breadcrumb"],
                "pages": c["pages"],
                "page_labels": c["page_labels"],
                "matched_path": p["path"],
                "matched_text": p["text"],
                "chunk_text": c["chunk_text"],
            })
        return hits
