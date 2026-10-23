# Search index (knowledge base steps 7–8)

Turns the knowledge base (handbook chunks plus BoldDesk help-center articles)
into the search index the chatbot loads.
No cloud services and no per-use cost: the embedding model runs on your
computer (and later on Render).

## Run it

```powershell
pip install -r requirements.txt
python build_index.py --chunks chunks\handbook_college.chunks.jsonl chunks\handbook_shs.chunks.jsonl --out index
python eval_gold.py --index index
```

The first run downloads the embedding model (about 130 MB) once.

Or use the **Search Index** tab in the app (`python app.py`): press **Sync from BoldDesk**, tick
the sources, press **Build search index**, and it saves a new `index_vN` folder with its Gate 4
result. (See README.md for the BoldDesk `.env` setup.)

Or the whole pipeline in one command, ending with the gold-question gate:

```powershell
python ingest.py --pdf-college MCM-HANDBOOK-2020-COLLEGE.pdf --pdf-shs MCM-HANDBOOK-2020-SHS.pdf --authored authored_tables.json --workdir build --embed
```

## What's different from the thesis version

| Thesis (v1) problem | Fix here |
|---|---|
| Motto ("Excellence and Relevance") could not be found: it was 3 words buried in a chunk about the mission | Each chunk is split into small passages that keep their label path, e.g. `[College Student Handbook > MISSION > SCHOOL MOTTO]`. Search finds the passage; the AI still gets the whole chunk. |
| College and SHS answers mixed | Search is filtered by audience before scoring. Help-center articles have audience `all` and are searched for every group. |
| Min-max score blending let meaning-search's #1 always win | Weighted Reciprocal Rank Fusion: 0.3 meaning, 0.7 keyword, k = 60 (chosen by sweep) |
| Only 10 candidates per method | 50 candidates per method |
| `"absences."` did not match `"absences"`; a trailing `?` matched nothing | New keyword tokenizer: strips punctuation, folds accents, keeps `1.25` and `137-a` |
| Embeddings file re-read on every question | Index loaded once at startup |
| No check that search works before going live | Gate 4 (in `ingest.py` and the app's Search Index tab): 90% of gold questions in the top 5, and the motto question must rank first |

## Files

| File | Job |
|---|---|
| `passages.py` | Splits a chunk into labelled passages |
| `build_index.py` | Builds the index folder (passages, vectors, manifest) |
| `retrieve.py` | Hybrid search used by the chatbot and the tests |
| `eval_gold.py` | Scores an index on the gold questions |
| `gold_expected.json` | The 33 questions (16 College, 4 SHS, 5 High School, 8 Help Center) and which chunks answer them |
| `bolddesk_sync.py` | Read-only sync of published, public help-center articles into `chunks/bolddesk_kb.chunks.jsonl` |

## Results so far

All runs use bge-small-en-v1.5, meaning share 0.3, number-word map on, MMCM rewrite on.

| Index | Sources | Gold questions | 1st | Top 3 | Top 5 | MRR |
|---|---|---|---|---|---|---|
| `index` (Oct 21) | College, SHS | 20 | 13 | 20 | 20 | 0.808 |
| `index_v1` (Oct 21) | + High School | 25 | 18 | 25 | 25 | 0.847 |
| `index_v2` (Oct 23) | + BoldDesk help center (32 articles) | 33 | 24 | 32 | 33 | 0.846 |

MRR is only comparable between runs on the same question set. Adding the 41 help-center chunks,
which compete in every audience, did not push the handbook answers down.

Lessons recorded along the way:
- Meaning share swept 0.0–0.6: 0.2 and 0.3 tied best (0.808), 0.6 was worse (0.742).
- Mapping "mmcm" to "mcm" on the keyword side hurt (0.779 to 0.746, keyword-only); removed.
- Help-center articles were first labelled "MMCM Help Center"; the word MMCM made them match any
  question mentioning MMCM ("What is MMCM's mission?" fell to #3). The label is now "Help Center".

## Rules

- Never edit the chunk files to improve search. Change passages or retrieval,
  rebuild, and compare with `eval_gold.py` (one change per run).
- Build into a new folder (`index_v2`), test it, then point the app at it.
- The model that built the index must be the model that embeds questions;
  `retrieve.py` reads it from `manifest.json`.

## Where the knowledge base lives

| Part | Where | Why |
|---|---|---|
| Handbook chunks (`chunks/handbook_*.jsonl`) | this repo | the reviewed corpus; git shows what changed between editions |
| Help-center articles | BoldDesk (source of truth); a synced copy in `chunks/bolddesk_kb.chunks.jsonl` | MMCM edits BoldDesk; the sync follows it |
| BoldDesk API key | local `.env` / the chatbot server's environment variables | never in git |
| Released index (`index_v1/`, `index_v2/`, ...) | the chatbot repo | the chatbot serves exactly the version that passed Gate 4; rolling back = pointing at the previous folder |
| Embedding model (~130 MB) | downloaded during Render's build | needed at answer time, identical every build, too big for git |
| Query logs | a database (to be chosen) | Render's disk is wiped on every deploy |
| GLM API key | Render environment variables / local `.env` | never in git |
