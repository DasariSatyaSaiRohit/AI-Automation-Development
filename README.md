# Prospect Research Agent

Production-ready FastAPI web app wrapping the hackathon `enrich_company()` pipeline.

## Run Locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000

## Deploy (Render / Railway)

1. Push this folder to a GitHub repo
2. On Render → New Web Service → connect repo
3. Set **Start command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Done — public URL provided automatically

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/enrich` | Submit URLs for enrichment (returns `job_id`) |
| `GET`  | `/job/{job_id}` | Poll enrichment job status |
| `GET`  | `/results` | Fetch all stored results |
| `DELETE` | `/results` | Delete one result (body: `{"url": "..."}`) |
| `DELETE` | `/results/all` | Wipe all results |

## Features

- **Multi-URL batch processing** — paste any number of URLs at once
- **Background jobs** — non-blocking, polled via `/job/{id}`
- **Shimmer loading state** — per-URL progress indicators
- **Persistent storage** — all results saved to `data/results.json`
- **Delete** individual records or clear all
- **CORS-enabled** — ready for frontend separation if needed
