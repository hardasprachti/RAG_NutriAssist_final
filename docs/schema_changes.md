# Structured Response Schema — Changes and Contract

Source of truth: [`backend/models/schemas.py`](../backend/models/schemas.py). The frontend mirror is
[`frontend/src/types/api.ts`](../frontend/src/types/api.ts); a test (`backend/tests/test_schemas.py`) fails if
the field names drift apart. The contract is **frozen** as of Phase 3: change both files together, and update this
page.

> **Open item — Milestone 1 baseline.** The Problem Statement (§5) asks for "changes from the Milestone 1
> schema", but the Milestone 1 schema itself is not in this repository, so it cannot be diffed here. The tables
> below are written against the schema in Problem Statement §5, which this project was given as its starting
> point. If Milestone 1 differed from that, add a row to the first table before the README is finalised (Phase 9).

## Response schema (what the LLM produces)

```json
{
  "answer": "string",
  "claims": [{ "claim_text": "string", "source": { "document_name": "", "publisher": "", "year": 2020,
                                                  "section": "", "url": "", "chunk_id": "" } }],
  "status": "answered | not_in_corpus | out_of_scope | error",
  "refusal_reason": "string | null"
}
```

Field names and types are exactly those of Problem Statement §5. Behavioural rules were added on top:

| Rule | Where enforced | Why |
|---|---|---|
| `answered` ⇒ `claims` is non-empty | `NutritionResponse` validator | An answer with no citation must never reach the UI as a verified answer |
| `answered` ⇒ `refusal_reason` is `null` | validator | Keeps the two states unambiguous for the frontend |
| not `answered` ⇒ `claims` is empty | validator | A refusal cannot carry "supporting" claims |
| not `answered` ⇒ `refusal_reason` is non-blank | validator | The UI always has an explanation to show |
| `answer`, `claim_text`, and every source field except `section` must be non-blank | `NonBlank` type | Blank citations are citations that cannot be verified |
| `year` is an integer | type | Matches the document registry |
| `source.section` may be an empty string | type | Some documents have no detectable heading; the chunk metadata is still exact |

Checks that need the retrieved chunks (the `chunk_id` exists in the retrieved set, source metadata equals the
chunk's, URL is in the corpus whitelist, numbers appear in the cited chunk) are not part of the schema; they run in
`core/response_validator.py` (Phase 4) and the **chunk's** metadata overrides whatever the model wrote.

The `error` status exists in the schema (Problem Statement §5) but is produced only by the system, never the model;
the system prompt tells the model not to use it.

## API additions

These do not exist in the Problem Statement §5 schema; they wrap it for the application (Architecture §10).

| Model | Notes |
|---|---|
| `ChatRequest` | `question` trimmed, 1–1000 chars, no NUL characters (PostgreSQL text cannot hold them); `conversation_id` is a UUID or `null` (new conversation) |
| `ChatResponse` | `NutritionResponse` + `conversation_id`, `message_id`, `retrieved_sources` (empty for refusals/errors) |
| `RetrievedSource` | One retrieved chunk with its full text, so the sources panel shows the real evidence excerpt; `rank` preserves retrieval order |
| `ConversationSummary`, `ConversationDetail`, `MessageOut` | History endpoints; `MessageOut` carries claims, status and sources so a reloaded conversation restores the sources panel |
| `HealthResponse`, `ErrorEnvelope` | `GET /api/health` (`status`, `vector_store`, `database`, `llm`; `database` added in Phase 5), and the body of every non-2xx response |
| `UpdateConversationRequest` | `PATCH /api/conversations/{id}` (rename, added with multi-chat): `title` is required, trimmed, 1-255 characters; unlike creation, blank is rejected |
| `X-Client-Id` header (not a body field) | Required on every conversation route and on `POST /api/chat`; a UUID the browser generates once. Backed by `conversations.owner_id` (migration `0002`, nullable: rows from before it belong to nobody) |
| `CreateConversationRequest` | `title` is optional, trimmed, at most 255 characters (the column size); blank means untitled |

Refusals are two distinct statuses, as required: `not_in_corpus` (evidence missing) and `out_of_scope` (request
not allowed, decided in backend code by `core/safety_validator.py` before any retrieval or LLM call).

## Strict-mode LLM schema

Groq's strict structured output (supported for `openai/gpt-oss-120b`) requires every property to be listed in
`required` and `additionalProperties: false` on every object. `integrations/llm_client.to_strict_schema` derives
that from the Pydantic model (`refusal_reason` becomes `string | null`, required) and drops keywords strict mode
does not accept (`default`, `title`, length bounds, …). Those constraints and the status invariants above are then
re-checked by Pydantic after parsing, so a model (or a fallback to plain JSON mode) cannot weaken them.
