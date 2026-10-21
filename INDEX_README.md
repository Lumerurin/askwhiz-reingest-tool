# Search index (knowledge base steps 7–8)

Turns the 132 frozen handbook chunks into the search index the chatbot loads.
No cloud services and no per-use cost: the embedding model runs on your
computer (and later on Render).

## Run it

```powershell
pip install -r requirements.txt
python build_index.py --chunks chunks\handbook_college.chunks.jsonl chunks\handbook_shs.chunks.jsonl --out index
python eval_gold.py --index index
```

The first run downloads the embedding model (about 130 MB) once.

Or use the **Search Index** tab in the app (`python app.py`): tick the handbooks, press
**Build search index**, and it saves a new `index_vN` folder with its Gate 4 result.

Or the whole pipeline in one command, ending with the gold-question gate:

```powershell
python ingest.py --pdf-college MCM-HANDBOOK-2020-COLLEGE.pdf --pdf-shs MCM-HANDBOOK-2020-SHS.pdf --authored authored_tables.json --workdir build --embed
```

## What's different from the thesis version

| Thesis (v1) problem | Fix here |
|---|---|
| Motto ("Excellence and Relevance") could not be found: it was 3 words buried in a chunk about the mission | Each chunk is split into small passages that keep their label path, e.g. `[College Student Handbook > MISSION > SCHOOL MOTTO]`. Search finds the passage; the AI still gets the whole chunk. |
| College and SHS answers mixed | Search is filtered by audience before scoring |
| Min-max score blending let meaning-search's #1 always win | Weighted Reciprocal Rank Fusion: 0.3 meaning, 0.7 keyword, k = 60 (chosen by sweep) |
| Only 10 candidates per method | 50 candidates per method |
| `"absences."` did not match `"absences"`; a trailing `?` matched nothing | New keyword tokenizer: strips punctuation, folds accents, keeps `1.25` and `137-a` |
| Embeddings file re-read on every question | Index loaded once at startup |
| No check that search works before going live | Gate 4 (in `ingest.py` and the app's Search Index tab): 18 of 20 gold questions in the top 5, and the motto question must rank first |

## Files

| File | Job |
|---|---|
| `passages.py` | Splits a chunk into labelled passages |
| `build_index.py` | Builds the index folder (passages, vectors, manifest) |
| `retrieve.py` | Hybrid search used by the chatbot and the tests |
| `eval_gold.py` | Scores an index on the 20 gold questions |
| `gold_expected.json` | The 20 questions and which chunks answer them |

## Results so far

Keyword-only (measured; the embedding model could not be downloaded in the
build environment): **hit@1 12/20, hit@3 18/20, hit@5 19/20, MRR 0.754**.
The motto question ranks its answer first.

Hybrid (keyword + meaning): run `eval_gold.py` on your machine and record the
numbers here. That run is the baseline every later change is measured against.

## Rules

- Never edit the chunk files to improve search. Change passages or retrieval,
  rebuild, and compare with `eval_gold.py` (one change per run).
- Build into a new folder (`index_v2`), test it, then point the app at it.
- The model that built the index must be the model that embeds questions;
  `retrieve.py` reads it from `manifest.json`.

## Where the knowledge base lives

| Part | Where | Why |
|---|---|---|
| Chunks (`chunks/*.jsonl`) | this repo | the source of truth; git shows what changed between editions |
| Released index (`index_v1/`, `index_v2/`, ...) | the chatbot repo | the chatbot serves exactly the version that passed Gate 4; rolling back = pointing at the previous folder |
| Embedding model (~130 MB) | downloaded during Render's build | needed at answer time, identical every build, too big for git |
| Query logs | a database (to be chosen) | Render's disk is wiped on every deploy |
| GLM API key | Render environment variables / local `.env` | never in git |
