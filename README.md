# Travel RAG Cloud Assistant (Azure)

A tourist assistant for Paris, built and deployed on Azure, that answers questions by **routing each question to the right data source**:

- **Structured facts** (ticket prices, opening hours, "cheapest attraction") are answered from **Azure SQL Database**.
- **Unstructured knowledge** (safety rules, transport tips, how to avoid queues) is retrieved from **Azure AI Search**.
- Both kinds of context go to **Azure OpenAI**, which writes the final answer **with citations** to its sources.

Built for the *Cloud Computing for Machine Learning* course (MSc, University of Bucharest). The assistant answers in Romanian.

![Python](https://img.shields.io/badge/Python-3.10-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Azure](https://img.shields.io/badge/Azure-App_Service_·_SQL_·_AI_Search_·_OpenAI-0078D4?logo=microsoftazure&logoColor=white)
![GitHub Actions](https://img.shields.io/badge/CI%2FCD-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)

---

## Architecture

```
                         POST /chat {"question": "..."}
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │  FastAPI on Azure         │
                        │  App Service              │
                        │  (keyword-based router)   │
                        └──────┬──────────────┬─────┘
           price / hours / day │              │ rules / tips / transport
                               ▼              ▼   (or SQL found nothing)
                ┌────────────────────┐  ┌──────────────────────────┐
                │ Azure SQL Database │  │ Azure AI Search          │
                │ attractions        │  │ chunked docs + 1536-dim  │
                │ opening_hours      │  │ embeddings (top 3)       │
                │ tickets            │  └──────────────────────────┘
                └─────────┬──────────┘              │
                          └──────────┬──────────────┘
                                     ▼
                         ┌───────────────────────┐
                         │ Azure OpenAI (chat)   │  temperature 0.2
                         │ "use SQL for numbers" │
                         └───────────┬───────────┘
                                     ▼
            {answer, citations[], execution_flow, latency_ms}
```

### Routing logic

| Question type | Example | Flow |
|---|---|---|
| Structured | "How much is an adult ticket at the Louvre?" | `SQL + LLM` |
| Unstructured | "How do I validate tickets on public transport?" | `SEARCH + LLM` |
| Mixed | "Is Orsay open on Tuesday, and what are the entry rules?" | `SQL + SEARCH + LLM` |
| Aggregation | "What is the cheapest attraction?" | `SQL` (`ORDER BY price` + `TOP 1`) |

If SQL returns nothing, the router falls back to search, so every question gets some context. All SQL queries are **parameterized** (`pyodbc` with `?` placeholders).

## Data

**Relational data (Azure SQL):** three tables, `attractions`, `opening_hours` (per weekday, with an `is_closed` flag) and `tickets` (ticket type, price, currency), joined on the attraction name. `sql/demo.sql` contains example analytical queries: attractions open on Monday, top 5 most expensive tickets, free attractions, and the weekly schedule for one attraction.

**Documents (Azure AI Search):** 10 curated `.txt` files in `docs/` (Louvre, Orsay, Eiffel Tower, Versailles, transport, museum rules, …). `app/index_docs.py` is the indexing pipeline:

1. Parse the metadata header (`TITLE`, `SOURCE`, `CITY`).
2. Split the text into chunks of **1,100 characters with 200 characters of overlap**.
3. Create embeddings with Azure OpenAI `text-embedding-3-small` (1536 dimensions), in batches of 16, checking that each vector has the expected size.
4. Upload to Azure AI Search in batches of 200, using a **deterministic SHA-1 ID** (`source:chunk_id`), so re-indexing overwrites existing chunks instead of duplicating them.

## API

| Method | Endpoint | Returns |
|---|---|---|
| POST | `/chat` | `answer`, `citations` (source + chunk), `execution_flow` (e.g. `SQL + SEARCH + LLM`), `latency_ms` |
| GET | `/health` | Status of each Azure dependency (live SQL connectivity check) |

## Performance benchmark

`benchmark.py` sends **20 test questions in 4 categories** (SQL-only, search-only, mixed, edge cases) to the deployed endpoint, several times each. It records latency and the route taken for each answer.

For each route it reports **mean, p50, p95 and max latency**, saved as CSV and JSON, plus charts (p50 vs p95 per route).

```bash
API_URL=https://<your-app>.azurewebsites.net/chat RUNS_PER_QUESTION=3 python benchmark.py
```

## Deployment (CI/CD)

`.github/workflows/main_webapp-rag.yml` builds and deploys to **Azure App Service** on every push to `main`. It authenticates to Azure with **OIDC federated credentials** (`azure/login`), so no long-lived secrets are stored in the repository.

## Running locally

**Requirements:** Python 3.10+, [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server), and access to the four Azure resources.

```bash
pip install -r requirements.txt

cat > .env <<'EOF'
AZURE_SEARCH_ENDPOINT=...
AZURE_SEARCH_INDEX=...
AZURE_SEARCH_ADMIN_KEY=...
AZURE_OPENAI_CHAT_ENDPOINT=...
AZURE_OPENAI_CHAT_KEY=...
AZURE_OPENAI_CHAT_DEPLOYMENT=...
AZURE_OPENAI_EMBED_ENDPOINT=...
AZURE_OPENAI_EMBED_API_KEY=...
AZURE_OPENAI_EMBED_DEPLOYMENT=...
SQL_SERVER=<server>.database.windows.net
SQL_DATABASE=...
SQL_USER=...
SQL_PASSWORD=...
EOF

python app/index_docs.py                 # one-off: chunk, embed and index docs/
uvicorn app.main:app --reload            # http://localhost:8000/docs
```

## Project structure

```
app/main.py            FastAPI app: router, SQL access, search, LLM call
app/index_docs.py      document indexing pipeline (chunk → embed → upload)
docs/                  source documents for retrieval
sql/demo.sql           example analytical queries
benchmark.py           latency benchmark per route
.github/workflows/     CI/CD to Azure App Service
```

## Limitations and next steps

- Routing uses keyword lists. An LLM or embedding-based intent classifier would handle phrasings the lists don't cover.
- The query path uses full-text search, although the index already stores vectors. The next step is **hybrid search** (BM25 + vector) with semantic ranking.
- The SQL schema (DDL) and seed data should be added to the repo, so the database can be rebuilt from scratch.
