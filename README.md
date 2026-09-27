# Onboard Agent

Onboard Agent is a local, repository-aware onboarding MVP. It accepts a public GitHub repository URL, creates a bounded source index, and exposes an onboarding overview, file and symbol browser, API route list, relationship-aware search, and follow-up chat.

The project uses Python's standard library, SQLite, Git, and browser-native HTML/CSS/JavaScript. It has no Python or Node package dependencies. Chat uses retrieved repository evidence even without a model key; setting an OpenAI-compatible API key enables model-generated explanations over a small set of redacted source excerpts.

## Start locally

Requirements:

- Python 3.11 or later
- Git on `PATH`
- Network access to GitHub when analyzing a repository

From the project root, start the combined API, worker, and static frontend server:

```powershell
python -m backend.main
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The server listens on loopback by default. SQLite data and shallow repository checkouts are created under `data/`.

No separate database, frontend dev server, or worker command is required. The UI, JSON API, and in-process analysis worker run in the same Python process.

To stop the service, press `Ctrl+C` in the server terminal.

## Analyze a repository

Use the dashboard's **Add repository** action, or submit the URL to the API:

```powershell
Invoke-RestMethod -Method Post `
  -Uri 'http://127.0.0.1:8000/api/repositories' `
  -ContentType 'application/json' `
  -Body '{"url":"https://github.com/owner/repository"}'
```

The response contains `repository_id` and `job_id`. Poll `GET /api/repositories/{repository_id}/status` until its `status` is `completed` or `failed`. Re-analysis is available at `POST /api/repositories/{repository_id}/analyze`.

Supported URLs must be HTTPS URLs on `github.com` in `owner/repository` form. The MVP does not support private repositories, GitHub Enterprise, branch selection, or local folders.

## Optional language model

The default chat mode is evidence-only. To enable generated explanations, configure an OpenAI-compatible Chat Completions endpoint before starting the server:

```powershell
$env:OPENAI_API_KEY = 'your-key'
$env:LLM_BASE_URL = 'https://api.openai.com/v1'
$env:LLM_MODEL = 'gpt-4.1-mini'
python -m backend.main
```

`OPENAI_API_KEY` is optional. `LLM_BASE_URL` and `LLM_MODEL` have defaults. When enabled, the service sends the question, recent conversation messages, and up to eight bounded, redacted repository excerpts to the configured endpoint. No embedding service is used.

## Analysis behavior

- Git downloads a shallow, single-branch checkout with submodules disabled, hooks redirected, global/system Git config disabled, and a clone timeout.
- The analyzer never runs repository code.
- It skips common generated/vendor directories, symlinks, binary files, and oversized files. It caps scanned files, directories, candidates, per-file size, and total indexed bytes.
- Python files use the standard-library AST for functions, classes, imports, route decorators, and name-resolved calls.
- JavaScript and TypeScript files use conservative text patterns for imports, declarations, and common HTTP route methods. Other supported text files are classified and indexed but not deeply parsed.
- SQLite stores sanitized file content, symbols, API route patterns, conversations, and file/symbol relationships. SQLite FTS5 provides keyword search when available; a bounded `LIKE` fallback is used otherwise.
- Search ranks lexical, path, and symbol matches, then may add directly related files. Embeddings, vector search, reranking, and automatic feature grouping are not implemented.
- Secret-value redaction covers common credential assignments, environment-file values, URL credentials, private-key blocks, and several recognizable token shapes. It is heuristic and cannot guarantee detection of every secret pattern.

## HTTP API

All API paths are same-origin under `/api`. JSON request bodies are limited to 256 KB. The default server binds to `127.0.0.1`; there is no user authentication or authorization.

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/health` | Service status and whether model chat is configured |
| `POST` | `/api/repositories/validate` | Validate a GitHub repository URL without cloning |
| `GET` | `/api/repositories` | List registered repositories and latest job state |
| `POST` | `/api/repositories` | Register a repository and queue analysis |
| `GET` | `/api/repositories/{id}` | Repository and latest job details |
| `GET` | `/api/repositories/{id}/status` | Analysis status |
| `POST` | `/api/repositories/{id}/analyze` | Queue a full re-analysis |
| `GET` | `/api/repositories/{id}/overview` | Counts, languages, README excerpt, and symbols |
| `GET` | `/api/repositories/{id}/guide` | Generated onboarding guide data |
| `GET` | `/api/repositories/{id}/files` | Search/list file metadata |
| `GET` | `/api/repositories/{id}/files/content?path=...` | Sanitized file content and extracted relationships |
| `GET` | `/api/repositories/{id}/symbols` | Search/list extracted symbols |
| `GET` | `/api/repositories/{id}/dependencies` | File and symbol relationship edges |
| `GET` | `/api/repositories/{id}/apis` | Discovered API route patterns |
| `GET` | `/api/repositories/{id}/search?query=...` | Evidence retrieval |
| `POST` | `/api/repositories/{id}/chat` | Repository-grounded question and follow-up |
| `GET` | `/api/repositories/{id}/conversations` | List saved conversations |
| `GET` | `/api/repositories/{id}/conversations/{conversation_id}` | Read a conversation and its citations |

The default API behavior has no login. Keep it on loopback; do not expose it to a network as a shared service without adding authentication, authorization, rate limits, and deployment controls.

## Tests and checks

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

Additional syntax checks:

```powershell
python -m compileall -q backend tests
node --check frontend/app.js
```

The repository lifecycle test uses a local fixture and a mocked clone function. A live GitHub clone and a real model-provider request were not part of the automated test run.

## Data and configuration

| Variable | Default | Purpose |
|---|---|---|
| `HOST` | `127.0.0.1` | HTTP bind address |
| `PORT` | `8000` | HTTP port |
| `ONBOARD_DATA_DIR` | `./data` | SQLite database and cloned repository storage |
| `CLONE_TIMEOUT_SECONDS` | `180` | Git clone timeout |
| `OPENAI_API_KEY` | unset | Enables optional model-generated chat |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `LLM_MODEL` | `gpt-4.1-mini` | Chat model name |

SQLite creates its schema on startup. There is no separate migration runner in this MVP. The `data/` directory is ignored by Git and contains downloaded repository files, so treat it as source-code data when managing backups or removing it.

## Current boundaries

This is a single-process local MVP, not a production multi-tenant SaaS. Analysis jobs are persisted but run in an in-process queue; jobs marked active during a server restart are changed to failed rather than resumed. Re-analysis downloads and scans the repository again. There is no authentication, private-repository support, persistent distributed queue, PostgreSQL, Redis, vector store, embeddings, or cross-language AST/call graph.

See [FINAL_REPORT.md](FINAL_REPORT.md) for the implementation inventory, API and data model details, test results, known issues, and next steps.
