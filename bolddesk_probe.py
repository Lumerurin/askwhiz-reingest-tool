#!/usr/bin/env python3
"""
bolddesk_probe.py - READ-ONLY first look at MMCM's BoldDesk knowledge base.

    python bolddesk_probe.py

What it does
    Lists the knowledge-base articles the API key can see and saves a summary
    (id, title, category, section, status, visibility, dates) to
    bolddesk_inventory.json. It does NOT download article text yet, and it
    never calls a ticket or contact endpoint: the only address it uses is
    GET /api/v1/kb/articles.

What it needs
    A file named .env in this folder (already in .gitignore, never commit it):
        BOLDDESK_DOMAIN=support.mcm.edu.ph
        BOLDDESK_API_KEY=paste-the-key-here

Why visibility matters
    BoldDesk articles can be public (Any Users) or limited to logged-in users,
    agents, or specific groups. AskWhiz is a public chatbot, so only articles
    that are Published AND visible to Any Users should ever be indexed. This
    probe shows how many of each there are so MMCM can confirm that rule.

API reference: https://developer.bolddesk.com/api/#tag/kbarticle
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "bolddesk_inventory.json"
PER_PAGE = 100          # BoldDesk's maximum page size

VISIBILITY = {1: "Any Users (public)", 2: "Logged-in users", 3: "Any agents",
              4: "Specific agent groups", 5: "Specific contact groups"}
STATE = {1: "Active", 2: "Deactivated", 3: "Deleted"}


def load_env():
    env = {}
    f = HERE / ".env"
    if not f.exists():
        sys.exit("! No .env file here. Create one with BOLDDESK_DOMAIN and BOLDDESK_API_KEY "
                 "(see the top of this script).")
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    domain = env.get("BOLDDESK_DOMAIN", "")
    domain = domain.replace("https://", "").replace("http://", "").strip("/")
    key = env.get("BOLDDESK_API_KEY", "")
    if not domain or not key:
        sys.exit("! .env needs both BOLDDESK_DOMAIN and BOLDDESK_API_KEY.")
    return domain, key


def get(domain, key, path, params):
    url = f"https://{domain}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"x-api-key": key, "accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                print("  rate limited by BoldDesk, waiting 60 s ...")
                time.sleep(60)
                continue
            msg = {
                401: "the API key was not accepted (wrong key, or it was revoked).",
                403: "access denied: the account that made this key has no Knowledge Base "
                     "permission in its role.",
                404: "address not found: check BOLDDESK_DOMAIN in .env.",
            }.get(e.code, e.read().decode("utf-8", "replace")[:300])
            sys.exit(f"! BoldDesk answered {e.code}: {msg}")
        except urllib.error.URLError as e:
            sys.exit(f"! Could not reach {domain}: {e.reason}")
    sys.exit("! Still rate limited after several tries; run again later.")


def name_of(x):
    return x.get("name") if isinstance(x, dict) else x


def main():
    domain, key = load_env()
    print(f"Listing knowledge-base articles on {domain} (read-only) ...")
    articles, page, total = [], 1, None
    while True:
        data = get(domain, key, "/api/v1/kb/articles",
                   {"Page": page, "PerPage": PER_PAGE, "RequiresCounts": "true"})
        batch = data.get("result") or []
        total = data.get("count", total)
        for a in batch:
            # keep only what we need: no author names or staff emails
            articles.append({
                "id": a.get("id"),
                "title": a.get("title"),
                "category": name_of(a.get("category")),
                "section": name_of(a.get("section")),
                "status": name_of(a.get("status")),
                "state": STATE.get(a.get("stateId"), a.get("stateId")),
                "visibility": VISIBILITY.get(a.get("visibilityOptionId"), a.get("visibilityOptionId")),
                "visibility_id": a.get("visibilityOptionId"),
                "published_on": a.get("publishedOn"),
                "last_modified_on": a.get("lastModifiedOn"),
                "portal_url": (a.get("articleSeo") or {}).get("canonicalUrl"),
            })
        print(f"  page {page}: {len(batch)} articles")
        if len(batch) < PER_PAGE or (total is not None and len(articles) >= total):
            break
        page += 1

    OUT.write_text(json.dumps({"domain": domain, "count": len(articles), "articles": articles},
                              indent=2, ensure_ascii=False), encoding="utf-8")

    usable = [a for a in articles if a["visibility_id"] == 1
              and str(a["status"]).lower().startswith("publish") and a["state"] == "Active"]
    print(f"\n{len(articles)} articles in total (BoldDesk reports {total}).")
    for label, key_ in (("By status", "status"), ("By visibility", "visibility"),
                        ("By state", "state"), ("By category", "category")):
        print(f"\n{label}:")
        for v, n in Counter(a[key_] for a in articles).most_common():
            print(f"  {n:>4}  {v}")
    print(f"\nUsable for AskWhiz (Published + Active + Any Users): {len(usable)}")
    for a in usable[:15]:
        print(f"  - [{a['category']} > {a['section']}] {a['title']}")
    if len(usable) > 15:
        print(f"  ... and {len(usable) - 15} more")
    print(f"\nSaved {OUT.name}. Nothing was changed in BoldDesk.")


if __name__ == "__main__":
    main()
