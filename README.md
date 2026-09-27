# Onboard Agent

Onboard Agent accepts a public GitHub repository URL, analyzes the repository comprehensively, stores the extracted knowledge and vector embeddings in PostgreSQL, and uses a real RAG pipeline to generate a detailed developer onboarding guide and answer questions about the codebase.

The goal: a new developer should be able to provide a GitHub repository and receive a clear, detailed explanation of what the project does, how it is structured, where each feature is implemented, how the important functions and classes work, and where to start reading the code.

## Requirements

- Python 3.11 or later
- Git on `PATH`
- PostgreSQL database (including [Supabase](https://supabase.com) free tier)
- Network access to GitHub when analyzing a repository

## Install dependencies

```powershell
pip install sqlalchemy alembic psycopg2-binary pgvector sentence-transformers fpdf2
```

`torch` is also required by `sentence-transformers`. Install it separately if needed:

```powershell
pip install torch
```

## Configure environment variables

Copy `.env.example` to `.env` and fill in the required values, or export them directly:

```powershell
# Required
$env:DATABASE_URL = 'postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres'

# LLM providers — at least one recommended for LLM-generated guides and chat answers
$env:IBM_API_KEY  = 'your-ibm-api-key'
$env:IBM_BASE_URL = 'https://...'      # your IBM-compatible endpoint
$env:IBM_MODEL    = 'ibm/granite-3-8b-instruct'

$env:GROQ_API_KEY = 'your-groq-api-key'   # fallback if IBM unavailable
```

See the full variable table below for all options.

## Apply database migrations

Run once before first use, and after any schema change:

```powershell
alembic upgrade head
```

To roll back one migration:

```powershell
alembic downgrade -1
```

## Start the server

```powershell
python -m backend.main
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The API, worker, and static frontend all run in the same process.

## Analyze a repository

Use the **Add repository** button in the dashboard, or call the API directly:

```powershell
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8000/api/repositories' `
  -ContentType 'application/json' `
  -Body '{"url":"https://github.com/owner/repository"}'
```

The response contains `repository_id` and `job_id`. Poll `GET /api/repositories/{id}` until `status` is `completed` or `failed`. Re-analysis is available at `POST /api/repositories/{id}/analyze`.

Only public HTTPS `github.com/owner/repository` URLs are accepted.

## What happens during analysis

1. The GitHub URL is validated: HTTPS only, `github.com` host, `owner/repository` form, no credentials.
2. Git performs a shallow, single-branch clone with submodules disabled, hooks redirected to an empty directory, and a configurable timeout.
3. The analyzer walks the checkout within hard limits (12,000 files, 750 KB per file, 80 MB total). It skips `node_modules`, `vendor`, `.git`, `dist`, build directories, symlinks, and binary files.
4. All file content is passed through secret redaction before storage: `.env` files are fully redacted, credential assignment patterns and URL passwords are masked, and token shapes (GitHub, AWS, Slack, JWT, Bearer) are replaced with `[REDACTED_TOKEN]`.
5. Each file is classified as `source`, `documentation`, `configuration`, `test`, or `other`.
6. Language-specific parsers extract symbols, imports, API routes, and call relationships. Python files use the standard-library AST. JavaScript/TypeScript, JSX/TSX, Vue, and Svelte use regex-based extraction. SQL files extract CREATE statements. Markdown files extract headings. All other recognized file types are indexed for keyword and vector retrieval even if they cannot be deeply parsed.
7. Semantic chunks are created: source files are chunked by function/class symbol; documentation files by heading section; files without symbols use a 50-line sliding window with 10-line overlap. Each chunk carries file path, line range, language, and symbol name.
8. The sentence-transformers embedding model (`all-MiniLM-L6-v2`, 384 dimensions) encodes every chunk into a normalized float vector and stores it in the `embeddings` table with an IVFFlat cosine index.
9. An onboarding guide is generated. If an LLM is configured, a structured context (README, directory structure, symbols, routes, dependencies, environment variables) is sent to the IBM provider with a system prompt requesting a comprehensive Markdown guide. If the LLM call fails or is not configured, a static Markdown guide is produced from the extracted data.
10. All results — files, symbols, relationships, routes, chunks, embeddings, and guide — are stored in PostgreSQL.

## Onboarding guide download

The generated guide is available as a downloadable file:

- `GET /api/repositories/{id}/guide/download?format=markdown` — Markdown file
- `GET /api/repositories/{id}/guide/download?format=pdf` — PDF file

Download buttons are also visible in the guide view of the dashboard.

## RAG chatbot

The chat interface uses hybrid retrieval to answer questions:

1. The question is embedded using the same model as the chunks.
2. pgvector cosine similarity retrieves the most semantically relevant chunks.
3. PostgreSQL ILIKE keyword search over chunks finds term matches.
4. ORM symbol name search finds symbols whose names match query terms.
5. The relationship graph adds chunks from files that import or are called from the top results.
6. Results are ranked by similarity score and deduplicated; up to `RETRIEVAL_TOP_K` (default 8) evidence items are selected.
7. The evidence plus conversation history are sent to the IBM LLM (with Groq as fallback). If no LLM is configured or the call fails, the evidence excerpts are returned directly with source citations.

Conversation context is preserved across follow-up questions. When the question contains deictic words ("that", "it", "there"), the previous user message is prepended to the retrieval query.

## Supported file types and languages

Deep symbol/import/route extraction:

- **Python** (AST): functions, classes, methods, imports, route decorators, name-resolved calls
- **JavaScript, TypeScript, JSX, TSX, Vue, Svelte** (regex): classes, functions, interfaces, types, enums, arrow functions, imports, require, HTTP route methods, Next.js pages
- **SQL** (regex): CREATE TABLE, VIEW, FUNCTION, PROCEDURE, INDEX, SEQUENCE, TYPE
- **Markdown/MDX/RST** (regex): heading sections

Indexed for keyword and vector search (no deep parse):

Java, Kotlin, Go, Rust, C, C++, C#, PHP, Ruby, Swift, HTML, CSS, SCSS, JSON, YAML, TOML, XML, INI, shell scripts, Terraform, Dockerfile, and all other recognized text extensions.

## HTTP API

All API paths are same-origin under `/api`. JSON bodies are limited to 256 KB. The server binds to `127.0.0.1` by default.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET`  | `/api/health` | Status, LLM availability, database health |
| `POST` | `/api/repositories/validate` | Validate a GitHub URL without cloning |
| `GET`  | `/api/repositories` | List registered repositories |
| `POST` | `/api/repositories` | Register a repository and queue analysis |
| `GET`  | `/api/repositories/{id}` | Repository status and latest job details |
| `POST` | `/api/repositories/{id}/analyze` | Queue a full re-analysis |
| `GET`  | `/api/repositories/{id}/overview` | Counts, languages, README excerpt, symbols |
| `GET`  | `/api/repositories/{id}/guide` | Onboarding guide (JSON + Markdown) |
| `GET`  | `/api/repositories/{id}/guide/download?format=markdown` | Download guide as Markdown |
| `GET`  | `/api/repositories/{id}/guide/download?format=pdf` | Download guide as PDF |
| `GET`  | `/api/repositories/{id}/files` | File list with optional path search |
| `GET`  | `/api/repositories/{id}/files/content?path=...` | Sanitized file content, symbols, relationships |
| `GET`  | `/api/repositories/{id}/symbols` | Symbol search and listing |
| `GET`  | `/api/repositories/{id}/dependencies` | Relationship graph edges |
| `GET`  | `/api/repositories/{id}/apis` | Detected API route patterns |
| `GET`  | `/api/repositories/{id}/search?query=...` | Hybrid RAG evidence retrieval |
| `POST` | `/api/repositories/{id}/chat` | RAG chatbot with conversation context |
| `GET`  | `/api/repositories/{id}/conversations` | List saved conversations |
| `GET`  | `/api/repositories/{id}/conversations/{conv_id}` | Conversation history and citations |

## Tests and checks

```powershell
# Run all tests (no DATABASE_URL required — DB-dependent code is mocked)
python -m unittest tests.test_analyzer tests.test_github tests.test_service tests.test_server -v

# Compilation check
python -m compileall -q backend tests alembic

# JavaScript syntax check
node --check frontend/app.js
```

The retrieval and hybrid-search tests additionally load the sentence-transformers model and require network access on first run (~90 MB download).

## Environment variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection string |
| `HOST` | No | `127.0.0.1` | HTTP bind address |
| `PORT` | No | `8000` | HTTP port |
| `ONBOARD_DATA_DIR` | No | `./data` | Clone storage directory |
| `CLONE_TIMEOUT_SECONDS` | No | `180` | Git clone timeout |
| `IBM_API_KEY` | No | — | Enables IBM LLM for guide generation and chat |
| `IBM_BASE_URL` | No | — | IBM-compatible Chat Completions base URL |
| `IBM_MODEL` | No | — | IBM model name |
| `GROQ_API_KEY` | No | — | Enables Groq as LLM fallback |
| `GROQ_BASE_URL` | No | `https://api.groq.com/openai/v1` | Groq API base URL |
| `GROQ_MODEL` | No | `openai/gpt-oss-20b` | Groq model name |
| `EMBEDDING_MODEL` | No | `sentence-transformers/all-MiniLM-L6-v2` | Hugging Face embedding model |
| `EMBEDDING_DIM` | No | `384` | Embedding dimension (must match model) |
| `RETRIEVAL_TOP_K` | No | `8` | Maximum chunks returned per RAG query |

`DATABASE_URL` is required. The application raises a `RuntimeError` at startup with a clear message if it is missing. All other variables are optional; the LLM and RAG chat features degrade gracefully when LLM keys are absent.

No API keys or secrets are exposed to the frontend. Credentials are read from environment variables or a `.env` file at the project root.

## Security

- Repository code is **never executed**. The analyzer reads file content as text data only.
- Git clones use HTTPS only, with submodules disabled, system and global Git configuration disabled, hooks redirected to an empty directory, and LFS smudge disabled.
- All file content passes through secret redaction before being stored or sent to any LLM.
- Path traversal in file content requests is blocked: paths containing `..` or absolute references are rejected.
- Database queries use the SQLAlchemy ORM and parameterized SQL; raw string interpolation is not used.
- The default server binds to `127.0.0.1`. Do not expose the unauthenticated API to a network.

## Current limitations

- There is no user authentication, private-repository support, or multi-tenant isolation.
- Analysis jobs are not resumed after a server restart; they are marked failed.
- Re-analysis downloads and scans the repository again; there is no incremental indexing.
- Secret redaction is heuristic and cannot guarantee detection of every credential pattern.
- Deep AST parsing is implemented for Python, SQL, and Markdown only. Other languages are indexed but not structurally parsed.
- The IVFFlat vector index performs well at scale but may use sequential scan for small repositories with fewer vectors than `lists` parameter.
- PDF generation uses latin-1 encoding; non-latin characters are replaced.

See [FINAL_REPORT.md](FINAL_REPORT.md) for the complete technical implementation report.
