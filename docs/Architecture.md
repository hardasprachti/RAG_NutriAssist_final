# 🏗️ AI-Powered Nutrition Assistant — System Architecture

> **Project:** Nutrition Assistant M2  
> **Document Type:** System Architecture & Design Reference  
> **Source:** Derived from [`problemStatement.md`](./problemStatement.md)

---

## Table of Contents

1. [High-Level System Overview](#1-high-level-system-overview)
2. [Layered Architecture](#2-layered-architecture)
3. [Technology Stack Decisions](#3-technology-stack-decisions)
4. [Frontend Architecture](#4-frontend-architecture)
5. [Backend Architecture](#5-backend-architecture)
6. [RAG Pipeline — Document Ingestion](#6-rag-pipeline--document-ingestion)
7. [RAG Pipeline — Query & Retrieval](#7-rag-pipeline--query--retrieval)
8. [Safety Enforcement Layer](#8-safety-enforcement-layer)
9. [Database Schema](#9-database-schema)
10. [API Contract Definitions](#10-api-contract-definitions)
11. [Structured Response Schema](#11-structured-response-schema)
12. [Knowledge Base — RAG Corpus](#12-knowledge-base--rag-corpus)
13. [Failure Logging Architecture](#13-failure-logging-architecture)
14. [Deployment Topology](#14-deployment-topology)
15. [Project Folder Structure](#15-project-folder-structure)
16. [Environment Variables](#16-environment-variables)

---

## 1. High-Level System Overview

```mermaid
graph TB
    subgraph Client["🖥️ Client (Browser)"]
        UI["Chat UI\n(Next.js / React + TypeScript)"]
    end

    subgraph Backend["⚙️ Backend (FastAPI / Next.js API Routes)"]
        SAFE["Safety Layer\n(Independent of LLM)"]
        RAG["RAG Pipeline\n(Retrieval + Generation)"]
        VAL["Response Validator\n(Schema Enforcement)"]
        LOG["Failure Logger"]
        LLM_INT["LLM Integration\n(Groq: gpt-oss-120b)"]
    end

    subgraph Data["🗄️ Data Layer"]
        VDB["Vector Database\n(Qdrant / pgvector / Pinecone)"]
        DB["Relational Database\n(Supabase / SQLite)"]
    end

    subgraph Corpus["📚 RAG Corpus (6 Documents)"]
        DOC1["WHO Healthy Diet"]
        DOC2["USDA Guidelines 2020-2025"]
        DOC3["FDA Storage Chart"]
        DOC4["UK Eatwell Guide"]
        DOC5["EFSA DRV Summary"]
        DOC6["ICMR India Guidelines"]
    end

    UI -- "HTTPS API Request" --> SAFE
    SAFE -- "Allowed" --> RAG
    SAFE -- "Blocked" --> UI
    RAG --> VDB
    RAG --> LLM_INT
    LLM_INT --> VAL
    VAL -- "Valid" --> UI
    VAL -- "Invalid" --> LOG
    LOG --> DB
    RAG --> DB
    VDB -.->|"Indexed from"| Corpus
```

> [!IMPORTANT]
> All LLM API calls happen **exclusively on the backend**. The browser never directly contacts the LLM provider.

---

## 2. Layered Architecture

The system is divided into four horizontal layers:

```mermaid
graph LR
    subgraph L1["Layer 1 — Presentation"]
        A1["Chat Interface"]
        A2["Sources Panel"]
        A3["Conversation History"]
    end

    subgraph L2["Layer 2 — Application (Backend API)"]
        B1["API Endpoints"]
        B2["Safety Validator"]
        B3["RAG Orchestrator"]
        B4["Schema Validator"]
        B5["Failure Logger"]
    end

    subgraph L3["Layer 3 — AI / ML"]
        C1["Embedding Model\n(bge-small-en-v1.5, local)"]
        C2["LLM (Groq gpt-oss-120b)"]
        C3["Retriever (top-k)"]
    end

    subgraph L4["Layer 4 — Storage"]
        D1["Vector DB\n(chunks + embeddings)"]
        D2["Relational DB\n(conversations, logs, metadata)"]
    end

    L1 --> L2
    L2 --> L3
    L3 --> L4
```

| Layer | Responsibilities |
|---|---|
| **Presentation** | Chat UI, sources panel, session display, responsive layout |
| **Application** | Request routing, safety enforcement, RAG orchestration, validation, logging |
| **AI / ML** | Embedding generation, semantic retrieval, LLM text generation |
| **Storage** | Persistent vector index, conversation history, failure logs, metadata |

---

## 3. Technology Stack Decisions

| Layer | Chosen Technology | Rationale |
|---|---|---|
| **Frontend** | Next.js + TypeScript | SSR support, API routes, type safety |
| **Backend** | FastAPI (Python) | Async support, easy LangChain integration, OpenAPI docs |
| **LLM** | Groq API (`openai/gpt-oss-120b`) | Open-weight model with fast inference, JSON-schema structured output; Pydantic validation as backstop |
| **Embeddings** | `BAAI/bge-small-en-v1.5` via sentence-transformers (local) | Groq offers no embeddings endpoint; small, strong retrieval quality, no per-call cost, 384-dim vectors |
| **Vector Database** | Qdrant (or Supabase pgvector) | Metadata filtering, HNSW index, open source |
| **Relational DB** | Supabase (PostgreSQL) | Managed, scalable, integrates with pgvector |
| **PDF Processing** | PyMuPDF + Docling | Reliable text extraction including tables |
| **RAG Framework** | LangChain | Chains, retrievers, prompt templates, output parsers |
| **Deployment** | Vercel (Frontend) + Railway (Backend) | Simple CI/CD, environment variable management |
| **Version Control** | GitHub | Required per project spec |

---

## 4. Frontend Architecture

### Component Tree

```mermaid
graph TD
    App["App Root (Next.js)"]
    App --> Layout["Layout Component"]
    Layout --> ChatPage["ChatPage"]
    ChatPage --> ConvSidebar["ConversationSidebar\n(session list)"]
    ChatPage --> ChatWindow["ChatWindow"]
    ChatPage --> SourcesPanel["SourcesPanel"]
    ChatWindow --> MessageList["MessageList"]
    ChatWindow --> InputBar["MessageInputBar"]
    MessageList --> UserBubble["UserBubble"]
    MessageList --> AssistantBubble["AssistantBubble"]
    AssistantBubble --> ClaimBadge["CitationBadge\n(per claim)"]
    SourcesPanel --> SourceCard["SourceCard\n(document + excerpt)"]
```

### Frontend Components

| Component | Responsibility |
|---|---|
| `ChatPage` | Top-level layout: sidebar + chat window + sources panel |
| `ConversationSidebar` | List previous sessions; start new conversations |
| `ChatWindow` | Renders message list and input bar |
| `MessageList` | Renders user and assistant messages in order |
| `AssistantBubble` | Displays structured response; highlights inline citations |
| `CitationBadge` | Superscript badge linking a claim to a source |
| `SourcesPanel` | Shows all retrieved documents and supporting excerpts |
| `SourceCard` | Displays document title, publisher, year, section, URL, excerpt |
| `MessageInputBar` | Text input, send button, loading state |
| `RefusalBanner` | Displays `not_in_corpus` or `out_of_scope` messages clearly |

### Frontend State Management

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Submitting : User sends message
    Submitting --> Loading : API call initiated
    Loading --> Answered : Valid response received
    Loading --> NotInCorpus : status = not_in_corpus
    Loading --> OutOfScope : status = out_of_scope
    Loading --> Error : status = error
    Answered --> Idle : Sources panel updated
    NotInCorpus --> Idle
    OutOfScope --> Idle
    Error --> Idle
```

---

## 5. Backend Architecture

### Backend Module Structure

```mermaid
graph TB
    subgraph API["API Layer (FastAPI)"]
        EP1["POST /api/chat"]
        EP2["GET /api/conversations"]
        EP3["GET /api/conversations/:id"]
        EP4["POST /api/conversations"]
        EP6["PATCH /api/conversations/:id"]
        EP7["DELETE /api/conversations/:id"]
        EP5["GET /api/health"]
    end

    subgraph Core["Core Services"]
        SAFETY["safety_validator.py\n(rule-based, LLM-independent)"]
        RAG["rag_pipeline.py\n(embed → retrieve → generate)"]
        VALID["response_validator.py\n(Pydantic schema check)"]
        LOGGER["failure_logger.py\n(writes to DB)"]
    end

    subgraph Integrations["External Integrations"]
        LLM["llm_client.py\n(Groq wrapper)"]
        EMBED["embedder.py\n(bge-small-en-v1.5)"]
        VSTORE["vector_store.py\n(Qdrant / pgvector client)"]
        DBCLIENT["db_client.py\n(SQLAlchemy / Supabase)"]
    end

    EP1 --> SAFETY
    SAFETY --> RAG
    RAG --> EMBED
    RAG --> VSTORE
    RAG --> LLM
    LLM --> VALID
    VALID --> LOGGER
    VALID --> EP1
    EP2 & EP3 & EP4 --> DBCLIENT
```

### Backend Request Flow

| Step | Module | Action |
|---|---|---|
| 1 | `POST /api/chat` | Receive user question + conversation ID |
| 2 | `safety_validator` | Check against restricted categories (rule-based) |
| 3 | `embedder` | Convert question to embedding vector |
| 4 | `vector_store` | Retrieve top-k relevant chunks |
| 5 | `rag_pipeline` | Build prompt with retrieved context only |
| 6 | `llm_client` | Call LLM, request structured JSON output |
| 7 | `response_validator` | Validate Pydantic schema, check citations |
| 8 | `failure_logger` | Log validation failures or missing citations |
| 9 | `db_client` | Persist message + retrieved chunks |
| 10 | Response | Return validated JSON to frontend |

---

## 6. RAG Pipeline — Document Ingestion

This pipeline runs **once** (or on document updates) to populate the vector store.

```mermaid
flowchart LR
    A["PDF / HTML\nDocuments"] --> B["PDF Extractor\n(PyMuPDF / Docling)"]
    B --> C["Text + Section Headings\nExtracted"]
    C --> D["Chunker\n(Recursive or Semantic)"]
    D --> E["Chunks with Metadata\n(doc_name, publisher, year, url, section, chunk_id)"]
    E --> F["Embedding Model\n(bge-small-en-v1.5)"]
    F --> G["Vector Embeddings"]
    G --> H["Vector Store\n(Qdrant / pgvector)"]
    E --> I["Document Metadata Table\n(Relational DB)"]
```

### Chunking Configuration

| Parameter | Recommended Value | Notes |
|---|---|---|
| Chunking Strategy | Recursive character splitting | Respects paragraph and section boundaries |
| Chunk Size | 512 tokens | Balances specificity and context |
| Chunk Overlap | 64 tokens | Preserves cross-boundary context |
| Embedding Model | `BAAI/bge-small-en-v1.5` (local) | 384 dimensions, 512-token max input; queries use the model's query prefix, normalised vectors |
| Vector Index | HNSW | Fast approximate nearest-neighbour search |
| Retrieval Top-k | 5 | Per query; adjustable per evaluation |

### Chunk Metadata Schema

Every chunk stored in the vector database must carry the following metadata:

```json
{
  "chunk_id": "who_healthy_diet_chunk_007",
  "document_name": "Healthy Diet Fact Sheet",
  "publisher": "World Health Organization (WHO)",
  "year": 2020,
  "source_url": "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
  "retrieval_date": "2025-10-05",
  "section": "Fats",
  "chunk_index": 7,
  "total_chunks": 42,
  "text": "...raw chunk text..."
}
```

> [!WARNING]
> Do **not** split tables, numbered recommendations, or structured lists across chunks. Preserve them as atomic units.

---

## 7. RAG Pipeline — Query & Retrieval

This pipeline runs **on every user query**.

```mermaid
flowchart TD
    A["User Question"] --> B{"Safety Validator\n(independent of LLM)"}
    B -- "out_of_scope" --> Z1["Return out_of_scope\nRefusal Response"]
    B -- "allowed" --> C["Embed Question\n(bge-small-en-v1.5)"]
    C --> D["Vector Store Query\n(top-k = 5)"]
    D --> E{"Document Filter\nRequested?"}
    E -- "Yes: specific doc" --> F["Filter by document_name\nmetadata field"]
    E -- "No: all docs" --> G["Return top-k across\nall documents"]
    F & G --> H["Retrieved Chunks\n(with full metadata)"]
    H --> I["Build RAG Prompt\n(context = retrieved chunks only)"]
    I --> J["LLM Call\n(structured JSON output)"]
    J --> K{"Pydantic Schema\nValidation"}
    K -- "fail" --> L["Log Failure\n(failure_logger)"]
    K -- "pass" --> M{"Citation\nVerification"}
    M -- "invalid citation" --> L
    M -- "all valid" --> N["Return Response\nto Frontend"]
    N --> O["Persist to DB\n(message + chunks + citations)"]
    L --> P["Return error status\nor sanitised response"]
```

### Context Window Management

```
System Prompt (fixed)
├── Assistant identity
├── Knowledge boundary rules
└── Safety boundary rules

User Context (dynamic, per query)
├── Retrieved Chunk 1 — [doc_name, section, text]
├── Retrieved Chunk 2 — [doc_name, section, text]
├── Retrieved Chunk 3 — [doc_name, section, text]
├── Retrieved Chunk 4 — [doc_name, section, text]
└── Retrieved Chunk 5 — [doc_name, section, text]

Conversation History (last N turns)
└── [human, assistant] message pairs

User Question
```

> [!IMPORTANT]
> Only retrieved chunk content is passed as factual context. The LLM's own parametric knowledge must never substitute for retrieved evidence.

---

## 8. Safety Enforcement Layer

The safety layer runs **before** any embedding, retrieval, or LLM call.

```mermaid
flowchart LR
    Q["Incoming Question"] --> R1{"Keyword / Pattern\nMatcher"}
    R1 -- "match: calorie target" --> S["out_of_scope"]
    R1 -- "match: weight target" --> S
    R1 -- "match: medical condition" --> S
    R1 -- "match: diet prescription" --> S
    R1 -- "no match" --> R2{"LLM-Based\nIntent Classifier\n(optional second pass)"}
    R2 -- "safe" --> ALLOW["Proceed to RAG"]
    R2 -- "unsafe" --> S
    S --> REFUSE["Return out_of_scope\nResponse\n(no LLM call)"]
```

### Safety Rule Categories

| Category | Detection Method | Examples Matched |
|---|---|---|
| Calorie targets | Regex + keyword | "how many calories", "calorie limit", "daily energy intake" |
| Weight targets | Regex + keyword | "ideal weight", "should I weigh", "target weight" |
| Medical dietary advice | Entity + keyword | "diabetes diet", "Crohn's disease", "cancer nutrition" |
| Personalised prescription | Pattern matching | "plan for me", "my diet", "custom meal plan" |

> [!CAUTION]
> The safety validator must operate **entirely in backend code**, independently of the LLM. An adversarial user cannot bypass it through prompt injection or conversation history manipulation.

### Adversarial Coverage

| Attack Type | Mitigation |
|---|---|
| Direct question | Keyword matching catches standard phrasing |
| Rephrasing | Synonym and semantic pattern set covers variations |
| Indirect framing | LLM-based intent classifier as second pass |
| Embedded in conversation | Safety check applied to **every** message, not just first |
| Follow-up after safe messages | Per-message check, not session-level |

---

## 9. Database Schema

### Entity Relationship Diagram

```mermaid
erDiagram
    CONVERSATIONS {
        uuid id PK
        string title
        timestamp created_at
        timestamp updated_at
    }

    MESSAGES {
        uuid id PK
        uuid conversation_id FK
        string role
        text content
        jsonb structured_response
        string status
        timestamp created_at
    }

    RETRIEVED_CHUNKS {
        uuid id PK
        uuid message_id FK
        string chunk_id
        string document_name
        string publisher
        int year
        string source_url
        string section
        text chunk_text
        float similarity_score
    }

    DOCUMENT_METADATA {
        uuid id PK
        string document_name
        string publisher
        int year
        string source_url
        date retrieval_date
        int total_chunks
        string embedding_model
    }

    FAILURE_LOGS {
        uuid id PK
        uuid message_id FK
        string failure_category
        text user_question
        text model_response
        jsonb retrieved_chunks
        text error_description
        string model_info
        timestamp created_at
    }

    EVALUATION_RESULTS {
        uuid id PK
        string question
        string expected_document
        string expected_section
        boolean hit_at_k
        int k_value
        float retrieval_score
        boolean citation_valid
        timestamp run_at
    }

    CONVERSATIONS ||--o{ MESSAGES : "contains"
    MESSAGES ||--o{ RETRIEVED_CHUNKS : "backed by"
    MESSAGES ||--o| FAILURE_LOGS : "may produce"
```

> **Implementation notes (Phase 1).** Two small deviations from the diagram above: `retrieved_chunks.rank` (int) was added so the sources panel is restored in retrieval order, and `failure_logs.message_id` is nullable (`ON DELETE SET NULL`) so failures from evaluation runs or unpersisted responses can still be logged. `document_metadata.source_url` is unique so ingestion can upsert idempotently. Schema is managed with Alembic (`backend/migrations/`).

### Table Descriptions

| Table | Purpose |
|---|---|
| `conversations` | One row per chat session |
| `messages` | All user and assistant messages; stores full structured response JSON |
| `retrieved_chunks` | Chunks retrieved for each assistant response |
| `document_metadata` | Registry of all ingested documents |
| `failure_logs` | All detected failures with full context |
| `evaluation_results` | Outputs of retrieval and citation evaluation runs |

---

## 10. API Contract Definitions

### POST `/api/chat`

**Request:**
```json
{
  "conversation_id": "uuid-or-null",
  "question": "How much saturated fat does WHO recommend?"
}
```

**Response (success):**
```json
{
  "conversation_id": "abc-123",
  "message_id": "msg-456",
  "answer": "WHO recommends limiting saturated fat to less than 10% of total energy intake.",
  "claims": [
    {
      "claim_text": "Saturated fat should be less than 10% of total energy intake.",
      "source": {
        "document_name": "Healthy Diet Fact Sheet",
        "publisher": "World Health Organization (WHO)",
        "year": 2020,
        "section": "Fats",
        "url": "https://www.who.int/news-room/fact-sheets/detail/healthy-diet",
        "chunk_id": "who_healthy_diet_chunk_012"
      }
    }
  ],
  "status": "answered",
  "refusal_reason": null,
  "retrieved_sources": [ ]
}
```

**Response (not in corpus):**
```json
{
  "conversation_id": "abc-123",
  "message_id": "msg-457",
  "answer": "The available guidance documents do not contain information on this topic.",
  "claims": [],
  "status": "not_in_corpus",
  "refusal_reason": "No relevant content found in: WHO Healthy Diet, USDA Guidelines, FDA Storage Chart, Eatwell Guide, EFSA DRV, ICMR Guidelines."
}
```

**Response (out of scope):**
```json
{
  "conversation_id": "abc-123",
  "message_id": "msg-458",
  "answer": "I'm unable to provide personal calorie or weight targets. Please consult a registered dietitian or healthcare professional.",
  "claims": [],
  "status": "out_of_scope",
  "refusal_reason": "Personal calorie targets are outside the scope of this assistant."
}
```

### Identifying the caller

Every conversation endpoint and `POST /api/chat` require an `X-Client-Id` header holding a UUID the browser generates once. Conversations carry an `owner_id` set from it, and every query is scoped to it; a conversation owned by someone else is reported as not found. This is anonymous ownership, not authentication.

### GET `/api/conversations`

Returns a list of all conversation sessions (id, title, created_at).

### GET `/api/conversations/:id`

Returns all messages for a specific conversation, including retrieved chunks.

### POST `/api/conversations`

Creates a new empty conversation session. Returns the new `conversation_id`.

### PATCH `/api/conversations/:id`

Renames a conversation (`{"title": "..."}`, 1-255 characters). Does not change `updated_at`, so the list order is unchanged.

### DELETE `/api/conversations/:id`

Deletes a conversation with its messages and retrieved chunks (204). Failure-log rows about its messages are kept with `message_id` set to null. Other conversations are unaffected: each conversation is independent, and history is loaded per `conversation_id`.

### GET `/api/health`

Returns system health status including vector store connectivity and LLM API availability.

---

## 11. Structured Response Schema

### Pydantic Model (Backend Validation)

```python
from pydantic import BaseModel
from typing import Optional, List
from enum import Enum

class ResponseStatus(str, Enum):
    answered      = "answered"
    not_in_corpus = "not_in_corpus"
    out_of_scope  = "out_of_scope"
    error         = "error"

class SourceReference(BaseModel):
    document_name: str
    publisher: str
    year: int
    section: str
    url: str
    chunk_id: str

class Claim(BaseModel):
    claim_text: str
    source: SourceReference

class NutritionResponse(BaseModel):
    answer: str
    claims: List[Claim]
    status: ResponseStatus
    refusal_reason: Optional[str] = None
```

### Validation Rules

| Rule | Enforcement |
|---|---|
| `claims` must not be empty when `status = answered` | Pydantic validator |
| Each `source.chunk_id` must exist in retrieved chunks | Post-validation check |
| `source.url` must match a known document URL | Whitelist check |
| `refusal_reason` must be present when `status != answered` | Pydantic validator |
| Numerical values in claims must match retrieved chunk text | Spot-check layer |

---

## 12. Knowledge Base — RAG Corpus

### Document Registry

| # | Document | Publisher | Year | Format | Source URL |
|---|----------|-----------|------|--------|------------|
| 1 | Healthy Diet Fact Sheet | WHO | 2020 | HTML / Web | https://www.who.int/news-room/fact-sheets/detail/healthy-diet |
| 2 | Dietary Guidelines for Americans, 2020–2025 | USDA / HHS | 2020 | PDF | https://www.dietaryguidelines.gov/sites/default/files/2020-12/Dietary_Guidelines_for_Americans_2020-2025.pdf |
| 3 | Refrigerator & Freezer Storage Chart | FDA | 2023 | PDF | https://www.fda.gov/media/74435/download |
| 4 | The Eatwell Guide | UK Food Standards Agency | 2018 | PDF | https://assets.publishing.service.gov.uk/media/69b3e02e9d8b52961a62b3bb/eatwell-guide-master-digital_Final.pdf |
| 5 | Summary of Dietary Reference Values | EFSA | 2017 | PDF | https://www.efsa.europa.eu/sites/default/files/assets/DRV_Summary_tables_jan_17.pdf |
| 6 | Dietary Guidelines for Indians | ICMR / NIN | 2011 | PDF | https://www.nin.res.in/downloads/DietaryGuidelinesforNINwebsite.pdf |

### Document Processing Pipeline

```mermaid
flowchart LR
    S1["Download PDF / Scrape HTML"] --> S2["Extract Raw Text\n(PyMuPDF / Docling)"]
    S2 --> S3["Detect Section Headings"]
    S3 --> S4["Recursive Chunking\n(512 tokens, 64 overlap)"]
    S4 --> S5["Attach Metadata\n(doc, pub, year, url, section, chunk_id)"]
    S5 --> S6["Generate Embeddings\n(bge-small-en-v1.5)"]
    S6 --> S7["Upsert to Qdrant\n(with metadata payload)"]
    S5 --> S8["Register in\nDocument Metadata Table"]
```

### Coverage by Topic

| Topic | Primary Source | Secondary Source |
|---|---|---|
| Daily nutrient recommendations | USDA Guidelines, EFSA DRV | WHO Fact Sheet |
| Food groups and plate balance | UK Eatwell Guide, USDA Guidelines | ICMR India Guidelines |
| Fat recommendations | WHO Fact Sheet | USDA Guidelines |
| Food refrigeration and storage | FDA Storage Chart | — |
| Indian dietary patterns | ICMR India Guidelines | — |
| European nutrient reference values | EFSA DRV Summary | — |
| Sugar and salt limits | WHO Fact Sheet | UK Eatwell Guide |

---

## 13. Failure Logging Architecture

### Failure Detection Points

```mermaid
flowchart TD
    A["LLM Response Received"] --> B{"Schema Validation\n(Pydantic)"}
    B -- "fail" --> LOG1["Log: schema_validation_failure"]
    B -- "pass" --> C{"Citation Check:\nchunk_id exists in\nretrieved set?"}
    C -- "fail" --> LOG2["Log: invalid_citation"]
    C -- "pass" --> D{"Numerical Claim\nConsistency Check"}
    D -- "flag" --> LOG3["Log: inconsistent_numerical_claim"]
    D -- "pass" --> E{"Safety Bypass\nCheck: was a\nrestricted Q answered?"}
    E -- "yes" --> LOG4["Log: missing_refusal"]
    E -- "no" --> F["Response Passed\n— Return to Frontend"]
```

### Failure Log Record

```json
{
  "id": "uuid",
  "message_id": "uuid",
  "failure_category": "invalid_citation",
  "user_question": "How much vitamin C does WHO recommend?",
  "model_response": "WHO recommends 90mg of vitamin C daily.",
  "retrieved_chunks": [
    { "chunk_id": "who_chunk_003", "text": "...", "section": "Micronutrients" }
  ],
  "error_description": "chunk_id 'who_chunk_999' cited in response does not exist in retrieved set.",
  "model_info": "groq/openai/gpt-oss-120b",
  "timestamp": "2025-10-05T17:30:00Z"
}
```

### Failure Categories Reference

| Code | Category | Trigger |
|---|---|---|
| `schema_validation_failure` | Pydantic validation failed | Missing required field or wrong type |
| `invalid_citation` | chunk_id not in retrieved set | LLM fabricated a citation |
| `fabricated_source` | URL not in corpus whitelist | LLM invented a document |
| `missing_refusal` | Restricted question was answered | Safety layer bypass |
| `not_in_corpus_answered` | Out-of-corpus answered as fact | LLM used parametric knowledge |
| `inconsistent_numerical_claim` | Number differs across repeated runs | Non-determinism failure |
| `empty_claims` | `status=answered` but no claims | LLM returned unsupported assertion |
| `vague_response` | Answer too vague to be useful | Quality threshold not met |

---

## 14. Deployment Topology

```mermaid
graph LR
    subgraph Vercel["☁️ Vercel (Frontend)"]
        FE["Next.js App\n(Static + SSR)"]
    end

    subgraph Railway["🚂 Railway (Backend)"]
        API["FastAPI App\n(Uvicorn)"]
        INGESTION["Ingestion Script\n(one-time / scheduled)"]
    end

    subgraph Supabase["🗄️ Supabase"]
        PG["PostgreSQL\n(conversations, messages, logs)"]
        PGV["pgvector extension\n(optional: vector store)"]
    end

    subgraph Qdrant["🔵 Qdrant Cloud (optional)"]
        QDRANT["Vector Collections\n(chunks + embeddings)"]
    end

    subgraph External["🌐 External APIs"]
        GROQ["Groq API\n(LLM: gpt-oss-120b)"]
    end

    FE -- "HTTPS API calls" --> API
    API --> PG
    API --> QDRANT
    API --> GROQ
    INGESTION --> QDRANT
    INGESTION --> PG
```

### Deployment Checklist

| Step | Action |
|---|---|
| Source code | Push to GitHub (public repo) |
| Frontend | Deploy to Vercel; set `NEXT_PUBLIC_API_URL` |
| Backend | Deploy to Railway; set all env vars |
| Vector store | Populate Qdrant via ingestion script |
| Database | Run migrations on Supabase |
| API keys | Store only in Railway / Vercel env vars, never in code |
| Smoke test | Run evaluation question bank against production URL |

---

## 15. Project Folder Structure

```
nutrition-assistant-m2/
├── frontend/                        # Next.js app
│   ├── src/
│   │   ├── app/                     # App router pages
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── AssistantBubble.tsx
│   │   │   ├── SourcesPanel.tsx
│   │   │   ├── SourceCard.tsx
│   │   │   ├── CitationBadge.tsx
│   │   │   └── ConversationSidebar.tsx
│   │   ├── hooks/
│   │   │   └── useChat.ts
│   │   └── types/
│   │       └── api.ts               # TypeScript types for API responses
│   └── package.json
│
├── backend/                         # FastAPI app
│   ├── main.py                      # FastAPI entry point
│   ├── routers/
│   │   ├── chat.py                  # POST /api/chat
│   │   └── conversations.py        # GET/POST/PATCH/DELETE /api/conversations
│   ├── core/
│   │   ├── safety_validator.py     # Rule-based safety checks
│   │   ├── rag_pipeline.py         # Orchestrates retrieval + generation
│   │   ├── response_validator.py   # Pydantic schema validation
│   │   └── failure_logger.py       # Logs failures to DB
│   ├── integrations/
│   │   ├── llm_client.py           # Groq (gpt-oss-120b) wrapper
│   │   ├── embedder.py             # Local embedding generation (sentence-transformers)
│   │   ├── vector_store.py         # Qdrant / pgvector client
│   │   └── db_client.py            # SQLAlchemy / Supabase client
│   ├── models/
│   │   ├── schemas.py              # Pydantic models (NutritionResponse, etc.)
│   │   └── db_models.py            # SQLAlchemy ORM models
│   ├── prompts/
│   │   └── system_prompt.txt       # System prompt definition
│   └── requirements.txt
│
├── ingestion/                       # One-time document processing
│   ├── download_documents.py       # Fetch PDFs and HTML sources
│   ├── extract_text.py             # PyMuPDF / Docling extraction
│   ├── chunker.py                  # Recursive chunking logic
│   ├── embedder.py                 # Embed chunks
│   ├── upload_to_vectorstore.py    # Upsert to Qdrant
│   └── document_registry.json     # Corpus document metadata
│
├── evaluation/                      # Testing and evaluation scripts
│   ├── retrieval_eval.py           # 15-question retrieval hit rate
│   ├── benchmark_questions.py      # 10 benchmark Q&A runs
│   ├── consistency_test.py         # 3x same question runs
│   ├── citation_checker.py         # Manual citation spot-check helper
│   └── questions/
│       ├── retrieval_questions.json
│       └── benchmark_questions.json
│
├── docs/
│   ├── problemStatement.md
│   ├── Architecture.md             # This file
│   └── evaluation_report.md        # Filled in post-testing
│
├── .env.example                    # Environment variable template
├── README.md
└── docker-compose.yml              # Optional local dev setup
```

---

## 16. Environment Variables

```bash
# ── LLM ─────────────────────────────────────────────
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
GROQ_REASONING_EFFORT=low
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIM=384

# ── Vector Database ──────────────────────────────────
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=...
QDRANT_COLLECTION_NAME=nutrition_chunks

# ── Relational Database ──────────────────────────────
DATABASE_URL=postgresql://user:password@host:5432/nutrition_db
SUPABASE_URL=https://xyz.supabase.co
SUPABASE_SERVICE_KEY=...

# ── Backend ──────────────────────────────────────────
BACKEND_SECRET_KEY=...
ALLOWED_ORIGINS=https://your-frontend.vercel.app

# ── Frontend (public, safe to expose) ────────────────
NEXT_PUBLIC_API_URL=https://your-backend.railway.app
```

> [!CAUTION]
> Never commit `.env` to version control. Only `.env.example` (with blank values) should be in the repository.

---

## Architecture Decision Log

| Decision | Options Considered | Chosen | Rationale |
|---|---|---|---|
| LLM Provider | OpenAI, Anthropic, Groq | Groq (`openai/gpt-oss-120b`) | Fast inference, open-weight model, JSON-schema structured output; Pydantic validation guards schema drift |
| Embedding Model | OpenAI text-embedding-3-small, bge-small-en-v1.5, all-MiniLM-L6-v2 | `BAAI/bge-small-en-v1.5` (local) | Groq has no embeddings API; strong retrieval for its size, no per-call cost, 384 dims |
| Vector Store | Qdrant, Pinecone, pgvector | Qdrant | Metadata filtering, free tier, open source |
| PDF Extraction | PyMuPDF, LlamaParse, Docling | PyMuPDF + Docling | PyMuPDF for speed; Docling for table-heavy PDFs |
| Chunking | Fixed-size, Recursive, Semantic | Recursive Character | Respects natural language boundaries |
| Backend Framework | FastAPI, Express, Next.js API routes | FastAPI | Native Python, async, LangChain integration |
| Database | SQLite, Supabase, MongoDB | Supabase (PostgreSQL) | Managed, pgvector option, relational integrity |
| Vector Store (final, Phase 0, 2026-10-05) | Qdrant Cloud, Supabase pgvector | Qdrant Cloud | Payload indexes for per-document filtering, keeps vector load off the relational DB; pgvector remains the fallback if the Qdrant free tier is a problem |
| Embedding dimension (final, Phase 0) | 384 (bge-small), 1536 (OpenAI) | 384 | Must match `EMBEDDING_DIM` and the Qdrant collection size; changing the model requires re-ingestion |
| Safety layer (Phase 3) | Rules only, LLM classifier only, rules + optional LLM pass | Regex/keyword rules always run first and always win; optional LLM classifier (`SAFETY_LLM_CLASSIFIER`) can only add refusals and fails closed on sensitive-looking input | Rules are deterministic, testable and independent of the model; they miss novel phrasings, which the LLM pass is for |
| LLM structured output (Phase 3) | Strict `json_schema`, `json_object` + prompt, tool calling | Strict `json_schema` (supported for gpt-oss-120b), auto-fallback to `json_object`; Pydantic always re-validates | Strict mode removes most schema drift; the fallback keeps the app working if Groq changes support |
| Ingestion extractors (Phase 2) | PyMuPDF + Docling for everything; per-document choice | HTML (WHO), PyMuPDF (USDA, Eatwell, ICMR), Docling (EFSA), a bespoke PyMuPDF word-geometry extractor (FDA) | Docling mis-decodes the FDA chart's font (letter "d" → U+FFFD) and finds no table; PyMuPDF decodes it correctly and the two-panel layout is rebuilt into one table per food category. Docling reads EFSA's tables well but mis-segments a few cells, repaired by a conservative rule and flagged in the ingestion report |
| Chunk embedding input (Phase 2) | Text only, section + text | `"<section>\n<text>"` embedded; stored `text` stays raw | Chunks like "less than 10 percent of calories" never name their topic; the 512-token budget covers the whole embedded input (incl. `[CLS]`/`[SEP]`), so nothing is truncated by the model |
| Table / list handling (Phase 2) | Never split; split by size | Atomic when they fit; otherwise split on row / item boundaries with the table header / list lead-in repeated | Tables up to ~900 tokens (EFSA) exceed the model's 512-token window; row-aligned parts stay self-contained |
| Not-in-corpus detection (Phase 4) | LLM status only; similarity threshold only; both | Cosine gate (0.58, no LLM call) **and** the model's `not_in_corpus` | Measured: unrelated questions score <=0.53, in-corpus >=0.69, nutrition-adjacent off-corpus 0.63-0.69. The gate is cheap and safe for the unrelated; the adjacent cases need the model. Threshold to be tuned in Phase 7/8 |
| Validation failure policy (Phase 4) | Return with a warning; always error; retry then error | One retry with the errors fed back, then `status=error`; every finding logged | Never returns an unverified `answered`; one retry recovers most slips (seen live) without hiding persistent failures |
| Citation handling (Phase 4) | Trust the model's source fields; overwrite from the chunk; reject any mismatch | Overwrite from the chunk; re-point a citation to another retrieved chunk only if that chunk fully supports the claim and the match is unambiguous | Tables are split into row-group chunks, so correct figures are often cited to a sibling; strict rejection turned correct answers into errors (seen live). Fabricated chunk ids are never re-pointed |
| Retrieval query (Phase 4) | Embed the question as asked; strip document names and comparison words when a filter scopes the search | Strip them (search text only) | Routing words pulled the embedding toward each document's boilerplate ("WHO and Eatwell on salt" missed both salt sections) |
| Backend runtime (Phase 0) | Python 3.11–3.13 | Python 3.13 (dev), full `requirements.txt` resolves on 3.13 | Matches local toolchain; pin the same minor version on Railway |

---

*Document derived from [`problemStatement.md`](./problemStatement.md) — Project Nutrition Assistant M2*
