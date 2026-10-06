# AI-Powered Nutrition Assistant (M2)

A RAG chatbot that answers nutrition questions **only** from six official guidance documents (WHO, USDA, FDA, UK Eatwell, EFSA, ICMR/NIN), with a verifiable citation for every claim. It declines personal calorie/weight targets and medical dietary advice.

> Status: under construction. See [docs/implementation_plan.md](docs/implementation_plan.md) for the phase plan and [docs/Architecture.md](docs/Architecture.md) for the design. The full README (RAG configuration, schema changes, safety design, limitations, live URL) is completed in Phase 9.

## Repository layout

| Path | Purpose |
|---|---|
| `frontend/` | Next.js + TypeScript chat UI |
| `backend/` | FastAPI app (safety, RAG pipeline, validation, persistence) |
| `ingestion/` | One-time document download, extraction, chunking, embedding, indexing |
| `evaluation/` | Retrieval, benchmark, consistency and citation-check scripts |
| `docs/` | Problem statement, architecture, plan, evaluation and failure reports |

## Local development

Prerequisites: Python 3.11+ (developed on 3.13), Node.js 20+.

```bash
cp .env.example .env        # then fill in values; never commit .env
```

**Backend** (http://localhost:8000, OpenAPI docs at `/docs`):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
pytest
```

**Frontend** (http://localhost:3000):

```bash
cd frontend
echo NEXT_PUBLIC_API_URL=http://localhost:8000 > .env.local   # optional; this is the default
npm install
npm run dev
```

To use the real pipeline without any hosted service (real embeddings, real Groq via `GROQ_API_KEY`, the corpus ingestion wrote to `ingestion/data/qdrant`, conversations in a local SQLite file), run this instead of `uvicorn`:

```bash
cd backend && python -m scripts.serve_local          # http://localhost:8000
```

**Frontend tests.**

```bash
cd frontend
npm test            # Vitest: the chat state machine (independent chats, retry, delete, URL) and components
npm run test:e2e    # Playwright in your installed Chrome, against the running backend above: smoke flow and edge cases (restricted variants, typos, other scripts, 429/503/504, owner isolation, two tabs, axe)
```

The e2e flow asks real questions, so it calls Groq (most refusal and guard cases stop before the model). Pass `E2E_BASE_URL` to test an already running frontend. The corner cases and fallbacks the system is built around, with their status and the test for each, are in [docs/edge_case.md](docs/edge_case.md). Two backend suites are opt-in because they need the real model and the local corpus: `RUN_LOCAL_CORPUS_TESTS=1 pytest tests/test_gate_measured.py` (measures the similarity gate on awkward input) and `RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_client.py -k live_smoke` (one Groq call).

**UI.** The layout follows `stitch_design/code.html`: sidebar 220px | chat | evidence panel 320px on desktop; the panel behind a pill (drawer) on laptop widths; an icon rail with drawers on tablet; one column with a hamburger, a sources pill and a 44px send button on phones (safe-area insets for notched iPhones). Where `stitch_design/DESIGN.md` (green palette) and the HTML (maroon palette) disagree, the HTML is followed, since it is the later recolouring. Only real data is shown: the design's sample profile, "Pro member", "PubMed index connected", "SHA-256 verified", evidence grades and follow-up chips have no counterpart in this system and are not rendered.

The home page is the chat. Chats work like ChatGPT's: **New chat** starts an independent conversation, the sidebar lists all of them (most recently active first), and each can be opened, renamed or deleted. Every chat keeps its own history, in-flight question, unsent text and selected sources, so you can ask in one chat, switch to another while the answer is on its way, and come back. The open chat is kept in the URL (`/#<conversation id>`), so reloads, bookmarks, the back button and a second browser tab on a different chat all work. The backend reads history per `conversation_id` only, so nothing is shared between chats.

## Data layer

Relational data (conversations, messages, retrieved chunks, failure logs, evaluation results, document registry) lives in PostgreSQL (Supabase); chunk embeddings live in Qdrant.

```bash
cd backend
# 1. Relational schema (reads DATABASE_URL; a plain postgresql:// URL is fine)
alembic upgrade head
# 2. Qdrant collection `nutrition_chunks` (384-d, cosine, HNSW, payload indexes). Safe to re-run.
python -m scripts.init_vector_store
```

**Tests.** `pytest` runs on SQLite and an in-memory Qdrant by default. To also test against real servers, start throwaway ones and set `TEST_DATABASE_URL` / `TEST_QDRANT_URL` (never point these at Supabase or production; the Postgres tests create and drop their own databases):

```bash
docker run -d --name nutrition-m2-test-pg -e POSTGRES_PASSWORD=testpw -p 55432:5432 postgres:16
docker run -d --name nutrition-m2-test-qdrant -p 56333:6333 qdrant/qdrant
export TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/postgres
export TEST_QDRANT_URL=http://localhost:56333
pytest
```

## Corpus ingestion

Run from the repository root (the backend's virtualenv). Download, extraction and chunking need no credentials; embedding needs the local model (~130 MB, downloaded on first use).

```bash
python -m ingestion.run_ingestion --dry-run          # download + extract + chunk + report, no embedding/upload
python -m ingestion.run_ingestion                    # everything, into QDRANT_URL and DATABASE_URL
python -m ingestion.run_ingestion --qdrant-path ingestion/data/qdrant --no-db   # fully local (embedded Qdrant)
python -m ingestion.sanity_check --qdrant-path ingestion/data/qdrant            # smoke-test retrieval
python -m pytest ingestion/tests
```

Steps: `download_documents` (fails loudly on any HTTP/format error; records `retrieval_date` in `ingestion/data/manifest.json`) → `extract_text` (HTML / PyMuPDF / Docling / the FDA chart extractor, cached in `ingestion/data/`) → `chunker` (512 tokens, 64 overlap, tables and lists atomic) → `embedder` → `upload_to_vectorstore` (idempotent: deterministic chunk ids, a document's old points are replaced) plus the `document_metadata` rows. Every run rewrites [docs/ingestion_report.md](docs/ingestion_report.md); manual chunk review is in [docs/ingestion_manual_qa.md](docs/ingestion_manual_qa.md). Downloaded files and caches live in the git-ignored `ingestion/data/`.

## RAG pipeline (question → validated answer)

`backend/core/rag_pipeline.py` runs, in order: **safety** (rules, before any embedding/retrieval/LLM call) → **retrieve** → **prompt** → **generate** (Groq, structured output) → **validate** → **log**. A response that fails validation is retried once with the errors fed back; if it fails again the caller gets `status=error`, never an unverified `answered`.

| Stage | What it does |
|---|---|
| Retrieval | Top-k (default 5). A document named in the question ("according to WHO", "the FDA chart") filters the search. A comparison ("differ", "compare", two documents named) retrieves per document and merges, so one document cannot crowd the rest out. Routing words are removed from the *search text* only. Chunks scoring under the similarity gate are dropped; if none remain the answer is `not_in_corpus` with **no LLM call** |
| Prompt | Fixed system prompt + one user message: documents searched, chunks grouped by document (chunk_id, section, URL), recent history (quoted, marked context-only), the question |
| Validation (`response_validator.py`) | Pydantic schema; every `chunk_id` was retrieved; document/publisher/URL are in the six-document whitelist; source metadata is **overwritten from the chunk**; every number in a claim appears in its cited chunk; every number in the summary appears in a claim; a claim may not name a different document than it cites; lexical support check. Findings are *blocking* (retry, then `error`) or *advisory* (logged, response returned) |
| Citation re-pointing | Tables are split into row-group chunks, so a correct figure is often cited to the wrong sibling. If another retrieved chunk fully supports the claim, the citation is re-pointed (logged as advisory). Fabricated chunk ids are never re-pointed, and ambiguous matches (e.g. male vs female table) stay rejected |
| Failure log (`failure_logger.py`) | Every rejection and flag is written to `failure_logs` (question, raw model output, retrieved chunks, category, description, model). A failed write is itself logged at ERROR with the full record |

Failure categories: `schema_validation_failure`, `invalid_citation`, `fabricated_source`, `empty_claims`, `missing_refusal`, `not_in_corpus_answered`, `inconsistent_numerical_claim` (written by the Phase 7 consistency tests), `vague_response`, plus the Problem Statement categories Architecture §13 omits: `unsupported_claim`, `missing_citation`, `incorrect_retrieval` (found by evaluation), `conflicting_guidance_error`, and `llm_error` for provider outages.

Settings (`.env.example`): `RETRIEVAL_TOP_K` (5), `RETRIEVAL_MIN_SCORE` (0.58), `HISTORY_TURNS` (3), `MAX_VALIDATION_RETRIES` (1). The similarity gate is deliberately coarse: on `bge-small`, unrelated questions score ≤0.53 and in-corpus questions ≥0.69, but nutrition-adjacent off-corpus ones (keto diet, vitamin K for dogs) score 0.63–0.69, so those rely on the model's own `not_in_corpus` status. Tune it with held-out data in Phase 7/8.

Try it (real embeddings, real Groq call; uses the local corpus from `ingestion/data/qdrant` when `QDRANT_URL` is unset, and a local SQLite file for failure logs when `DATABASE_URL` is unset):

```bash
cd backend
python -m scripts.ask "How long can raw chicken be kept in the refrigerator?"
python -m scripts.ask --batch scripts/phase4_questions.json --report ../docs/phase4_exit_gate_run.md
```

Known limits (Phase 8 material): the number check is membership in the cited chunk, not row-level, so a figure from another row of the same table passes; the lexical support check is crude; the model is not deterministic (a status can differ between runs of the same question); there is no entailment check.

## Evaluation (Phase 7)

`evaluation/` measures the system; results land in `evaluation/results/` and are assembled into [docs/evaluation_report.md](docs/evaluation_report.md). Run from the repo root with the backend's virtualenv. Each script that talks to the API can start its own local backend (`--spawn-server`, real embeddings, real Groq call, local corpus, rate limits raised) or test any deployment with `--base-url`.

| Script | Measures | Needs |
|---|---|---|
| `python -m evaluation.retrieval_eval` | Retrieval hit rate on 40 questions with a known expected document and section (no LLM; misses classified `gate` / `wrong_document` / `wrong_section`) | local corpus |
| `python -m evaluation.benchmark_questions --spawn-server` | 23 questions in the four required categories plus off-corpus probes, restricted prompts and cross-document questions, through the real `/api/chat`; each response audited against the ingested chunks | `GROQ_API_KEY` |
| `python -m evaluation.consistency_test --spawn-server` | Same question 3x: figures, cited documents/sections, evidence, stability, refusal. Changed figures are logged as `inconsistent_numerical_claim` | `GROQ_API_KEY` |
| `python -m evaluation.safety_eval --spawn-server` | Adversarial matrix end to end (direct, rephrased, indirect, injection, typo, mixed, multi-turn) plus legitimate questions that must not be blocked | `GROQ_API_KEY` |
| `python -m evaluation.citation_checker` | Worksheet for the manual citation spot-check: claim, cited chunk, URL, and the page of the downloaded source where it is found | benchmark results |
| `python -m evaluation.report` | Builds `docs/evaluation_report.md` from the latest results, the manual reviews and `results/findings.md` | |
| `python -m pytest evaluation/tests` | The evaluation's own checks (each corruption category must be caught) | |

The question files in `evaluation/questions/` were written from the source documents, not from the system's output. Retrieval questions carry a `dev` / `heldout` split so Phase 8 tuning can be checked against questions that were never tuned on. The scoring code in `evaluation/scoring.py` is deliberately independent of `backend/core/response_validator.py`: the validator is the system under test.

## Backend core services

| Module | Role |
|---|---|
| `backend/models/schemas.py` | Structured response schema and API contract (changes: [docs/schema_changes.md](docs/schema_changes.md)); mirrored in `frontend/src/types/api.ts` |
| `backend/core/safety_validator.py` | LLM-independent safety rules for calorie targets, weight targets, disease-specific diets and personalised prescriptions; returns a ready `out_of_scope` response with no retrieval or LLM call. Optional LLM second pass (`SAFETY_LLM_CLASSIFIER=true`) can only add refusals |
| `backend/integrations/embedder.py` | Local `BAAI/bge-small-en-v1.5` (384-d, normalised); query prefix for questions only; loaded once at startup (`PRELOAD_MODELS`) |
| `backend/integrations/llm_client.py` | Groq `openai/gpt-oss-120b` with strict JSON-schema output, retries/backoff, JSON-mode fallback; reasoning text is never used |
| `backend/prompts/system_prompt.txt` | The fixed system prompt |

The safety rules are deliberately conservative and are regex-based, so they can miss phrasings they were not written for; the optional LLM pass and the system prompt are the second line of defence. They also over-refuse a few borderline general questions (for example daily calorie recommendations phrased for "adults").

Opt-in tests, skipped by default:

```bash
RUN_MODEL_TESTS=1 pytest tests/test_embedder.py                         # downloads the embedding model (~130 MB)
GROQ_API_KEY=... RUN_LIVE_LLM_TESTS=1 pytest tests/test_llm_client.py   # a few real Groq tokens
```

## API (Phase 5)

Every conversation route and `POST /api/chat` need an `X-Client-Id` header: a random UUID the browser creates once and keeps in `localStorage` (`frontend/src/lib/clientId.ts`). Conversations are owned by it, so one visitor cannot list, read, rename, delete or continue another's; another visitor's conversation answers 404 exactly like one that does not exist, and a missing or malformed header is a 422. It is a bearer secret, not a login: whoever holds the id holds the chats, and clearing site data starts an empty history. `/api/health` needs no header. Conversations stored before owners existed (migration `0002`) belong to nobody and are no longer listed.

Interactive docs at `/docs` (OpenAPI at `/openapi.json`). Every non-2xx response has the same body, `{"error": "<code>", "message": "...", "detail": "..."|null}`. Refusals (`out_of_scope`, `not_in_corpus`) and verification failures (`error`) are **200s** with that `status`, not HTTP errors.

| Endpoint | Purpose | Errors |
|---|---|---|
| `POST /api/chat` | One turn: safety → retrieve → generate → validate → store. `conversation_id: null` starts a conversation, titled from the first question | 404 unknown conversation · 422 empty / over 1000 chars / malformed · 429 · 503 · 504 |
| `POST /api/conversations` | Optional empty conversation (`title` ≤255 chars) → 201 | 422 · 429 |
| `GET /api/conversations?limit=` | Most recently active first (default 50, max 100) | 422 · 429 |
| `PATCH /api/conversations/{id}` | Rename (`title` 1-255 chars, not blank); not counted as activity, so the list order is unchanged | 404 · 422 · 429 |
| `DELETE /api/conversations/{id}` | Delete the conversation with its messages and retrieved chunks (permanent) → 204; failure logs about it are kept, unlinked | 404 · 422 · 429 |
| `GET /api/conversations/{id}` | Messages in order, with claims and, for answered ones, the retrieved sources and excerpts (restores the sources panel) | 404 · 422 · 429 |
| `GET /api/health` | `vector_store`, `database`, `llm`. **503** if the vector store or database is down; an LLM problem alone is `200` with `status: "degraded"`, so a provider blip does not fail a deploy check | |

How a turn is stored: history is read, the pipeline runs with no transaction open, then the user message, the assistant message (with its structured response), its retrieved chunks and its failure-log rows are written in **one transaction**. A request that fails (503) or times out (504) leaves nothing half-written (no empty conversation, no question without an answer). Failures the pipeline recorded are still stored, unlinked, when the turn itself could not be. Refusals and `error` responses are stored like any other message. `retrieved_sources` is returned (and restored on reload) only for `answered` messages, per the contract; the chunks behind a model-side `not_in_corpus` are stored for evaluation but not shown.

Safety runs on **every** message: `SafetyValidator.check(message, previous_user_message)` is called by the pipeline with the previous user turn read from the database, so history can never switch it off (tests: a restricted question after a safe one, after five safe turns, and an elliptical "and for me?").

| Setting | Default | Effect |
|---|---|---|
| `RATE_LIMIT_CHAT_PER_MINUTE` | 20 | per client IP, sliding window; 0 disables. Over the limit → 429 with `Retry-After` |
| `RATE_LIMIT_API_PER_MINUTE` | 120 | the other endpoints (health is never limited) |
| `CHAT_TIMEOUT_SECONDS` | 90 | wall clock for one chat turn → 504; the abandoned turn is dropped, not stored |
| `CHAT_MAX_CONCURRENCY` | 4 | pipelines running at once; the rest wait inside the timeout |

Known limits of this layer: the rate limiter is in process memory (resets on restart, not shared across instances), and behind a proxy it sees the proxy's address unless uvicorn is started with `--proxy-headers --forwarded-allow-ips=<proxy>` (Phase 9). Request bodies over 16 KB are refused by `Content-Length`; chunked uploads without one are bounded only by the question length limit. Conversations are scoped to an anonymous per-browser id (see above); there are no accounts.

## Configuration

All settings come from environment variables; see [.env.example](.env.example). `ALLOWED_ORIGINS` (comma-separated) controls CORS and defaults to `http://localhost:3000`.
