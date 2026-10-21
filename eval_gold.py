#!/usr/bin/env python3
"""
eval_gold.py - score an index against the 20 gold questions.

    python eval_gold.py --index index                  # hybrid, default balance
    python eval_gold.py --index index --no-dense       # keyword only
    python eval_gold.py --index index --w-dense 0.4    # 40% meaning, 60% keyword
    python eval_gold.py --index index --sweep          # compare several balances
    python eval_gold.py --index index --no-term-map    # without third->three matching
    python eval_gold.py --index index --no-rewrite     # meaning search sees 'MMCM' as typed

For each question it finds where the first expected chunk lands in the top 5.
Reports hit@1, hit@3, hit@5 and MRR, and saves every result to a JSON file so
the team can compare runs. Change ONE thing per run (rule 1).
"""

import argparse
import json
from pathlib import Path

import retrieve
from retrieve import Retriever


def run(r, gold, verbose=True):
    rows, rr = [], []
    for g in gold:
        hits = r.search(g["q"], audience=g["audience"], k=5)
        ids = [h["chunk_id"] for h in hits]
        rank = next((i + 1 for i, c in enumerate(ids) if c in g["expect"]), None)
        rr.append(1 / rank if rank else 0)
        rows.append({"q": g["q"], "rank": rank, "top": ids,
                     "top_match": hits[0]["matched_path"] if hits else None})
        if verbose:
            mark = {1: "HIT ", None: "MISS"}.get(rank, f"#{rank}  ")
            print(f"{mark} {g['q'][:62]:<62} -> {hits[0]['matched_path'][:70] if hits else '-'}")
    n = len(gold)
    at = lambda k: sum(1 for x in rows if x["rank"] and x["rank"] <= k)
    summary = {"hit@1": at(1), "hit@3": at(3), "hit@5": at(5), "mrr": round(sum(rr) / n, 3), "n": n}
    return rows, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="index")
    ap.add_argument("--gold", default="gold_expected.json")
    ap.add_argument("--no-dense", action="store_true")
    ap.add_argument("--w-dense", type=float, default=retrieve.W_DENSE,
                    help="share of the meaning search in the blend, 0 to 1 (default 0.3)")
    ap.add_argument("--sweep", action="store_true",
                    help="compare meaning shares 0.0, 0.2, 0.3, 0.4, 0.5, 0.6")
    ap.add_argument("--no-term-map", action="store_true",
                    help="turn off third->three style word matching (keyword side)")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="turn off spelling out MMCM for the meaning search")
    ap.add_argument("--save", default=None)
    args = ap.parse_args()

    retrieve.USE_TERM_MAP = not args.no_term_map
    retrieve.USE_QUERY_REWRITE = not args.no_rewrite
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    r = Retriever(args.index, use_dense=not args.no_dense, w_dense=args.w_dense)

    if args.sweep:
        if not r.use_dense:
            raise SystemExit("--sweep needs the meaning model; drop --no-dense")
        print(f"{'meaning share':>14}  hit@1  hit@3  hit@5   MRR")
        for w in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6):
            r.w_dense, r.w_sparse = w, 1 - w
            _, s = run(r, gold, verbose=False)
            print(f"{w:>14.1f}  {s['hit@1']:>5}  {s['hit@3']:>5}  {s['hit@5']:>5}  {s['mrr']:.3f}")
        return

    rows, s = run(r, gold)
    mode = f"hybrid w_dense={args.w_dense}" if r.use_dense else "keyword"
    print(f"\n[{mode}] term map {'on' if retrieve.USE_TERM_MAP else 'off'}, "
          f"rewrite {'on' if retrieve.USE_QUERY_REWRITE else 'off'} | "
          f"{r.manifest['passage_count']} passages, model={r.manifest['model']}")
    print(f"hit@1 {s['hit@1']}/{s['n']}   hit@3 {s['hit@3']}/{s['n']}   "
          f"hit@5 {s['hit@5']}/{s['n']}   MRR {s['mrr']:.3f}")

    out = args.save or ("eval_hybrid.json" if r.use_dense else "eval_keyword.json")
    Path(out).write_text(json.dumps({"mode": mode, "term_map": retrieve.USE_TERM_MAP,
                                     "manifest": r.manifest, "summary": s, "results": rows},
                                    indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved {out}")


if __name__ == "__main__":
    main()
