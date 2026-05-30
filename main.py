# ================================
# 🏆 Hackathon Template Notebook
# Prospect Research Agent
# ================================
import re
import json
import time
import random
import asyncio
import requests
import uuid
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import google.generativeai as genai
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()  # loads .env file when running locally

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

# ─── CONFIG ───────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("GEMINI_API_KEY", "")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY environment variable is not set.")
genai.configure(api_key=API_KEY)

DATA_FILE = Path("data/results.json")
DATA_FILE.parent.mkdir(exist_ok=True)
if not DATA_FILE.exists():
    DATA_FILE.write_text("[]")

executor = ThreadPoolExecutor(max_workers=5)

# ========= REQUIRED FUNCTION — UNCHANGED =========
def enrich_company(url: str) -> dict:
    """
    Input: Company URL
    Output: Structured company profile (STRICT FORMAT)
    """

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    TARGET_KEYWORDS = ["about", "contact", "services", "team", "company", "who-we-are", "what-we-do", "solutions"]

    def fetch_page(page_url, retries=2):
        for attempt in range(retries):
            try:
                time.sleep(random.uniform(0.8, 1.5))
                resp = requests.get(page_url, headers=HEADERS, timeout=12, allow_redirects=True)
                if resp.status_code == 200:
                    return resp.text
            except Exception:
                pass
        return None

    def clean_html(html):
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav",
                          "aside", "form", "iframe", "svg", "img", "button", "input"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 30]
        return "\n".join(lines[:300])  # cap ~300 lines for token safety

    def get_sitemap_urls(base_url):
        found = []
        for path in ["/sitemap.xml", "/sitemap_index.xml"]:
            try:
                r = requests.get(base_url.rstrip("/") + path, headers=HEADERS, timeout=8)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, "xml")
                    found = [loc.text for loc in soup.find_all("loc")]
                    if found:
                        break
            except Exception:
                pass
        return found

    def fuzzy_match(href, keywords):
        href_lower = href.lower()
        return any(kw in href_lower for kw in keywords)

    def get_relevant_links(base_url, homepage_html):
        soup = BeautifulSoup(homepage_html, "html.parser")
        links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(base_url, href)
            # Stay on same domain
            if urlparse(full).netloc == urlparse(base_url).netloc:
                if fuzzy_match(full, TARGET_KEYWORDS):
                    links.add(full)
        return list(links)[:6]  # cap at 6 relevant pages

    # --- SCRAPING STRATEGY ---
    base_url = url.rstrip("/")
    all_text_chunks = []

    # Step 1: Scrape homepage
    homepage_html = fetch_page(base_url)
    if homepage_html:
        all_text_chunks.append(f"[Homepage: {base_url}]\n{clean_html(homepage_html)}")

    # Step 2: Try sitemap first, fallback to link extraction
    relevant_urls = []
    sitemap_urls = get_sitemap_urls(base_url)
    if sitemap_urls:
        relevant_urls = [u for u in sitemap_urls if fuzzy_match(u, TARGET_KEYWORDS)][:5]
    elif homepage_html:
        relevant_urls = get_relevant_links(base_url, homepage_html)

    # Step 3: Scrape each relevant page
    for page_url in relevant_urls:
        html = fetch_page(page_url)
        if html:
            chunk = clean_html(html)
            if chunk:
                all_text_chunks.append(f"[Page: {page_url}]\n{chunk}")

    # Combine and truncate to ~12000 chars for token safety
    combined_text = "\n\n".join(all_text_chunks)[:12000]

    if not combined_text.strip():
        # Return empty schema if no content scraped
        return {
            "website_name": urlparse(base_url).netloc,
            "company_name": "N/A",
            "address": "N/A",
            "mobile_number": "N/A",
            "mail": [],
            "core_service": "N/A",
            "target_customer": "N/A",
            "probable_pain_point": "N/A",
            "outreach_opener": "N/A"
        }

    # --- AI EXTRACTION ---
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={"temperature": 0.1, "response_mime_type": "application/json"}
    )

    prompt = f"""You are a B2B research analyst. Extract structured data from the website content below.

WEBSITE URL: {url}

SCRAPED CONTENT:
{combined_text}

Return ONLY a valid JSON object with these exact fields. Do NOT hallucinate or invent data.
If a field is not found in the content, use "" for strings or [] for arrays.

{{
  "website_name": "Brand/site name as shown on the website",
  "company_name": "Full legal or official company name",
  "address": "Full physical address if found, else ''",
  "mobile_number": "Phone number if found, else ''",
  "mail": ["list", "of", "email", "addresses", "found"],
  "core_service": "Primary service or product offering in 1 sentence",
  "target_customer": "Who their ideal customers are based on site content",
  "probable_pain_point": "Likely business challenge their customers face, inferred from their offerings",
  "outreach_opener": "A personalized 2-sentence cold outreach opener referencing something specific from the site"
}}

Rules:
- Only extract contact info explicitly present in the scraped text
- Do not fabricate phone numbers, emails, or addresses
- outreach_opener must reference something specific and real from the content
- Return raw JSON only, no markdown, no explanation"""

    response = model.generate_content(prompt)
    raw = response.text.strip()

    # Strip markdown fences if present
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Attempt to extract JSON object from response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            result = {}

    # Enforce schema — fill missing keys with safe defaults
    schema_defaults = {
        "website_name": urlparse(base_url).netloc,
        "company_name": "N/A",
        "address": "N/A",
        "mobile_number": "N/A",
        "mail": [],
        "core_service": "N/A",
        "target_customer": "N/A",
        "probable_pain_point": "N/A",
        "outreach_opener": "N/A"
    }

    for key, default in schema_defaults.items():
        if key not in result or result[key] in [None, ""]:
            result[key] = default

    # Ensure mail is always a list
    if isinstance(result["mail"], str):
        result["mail"] = [result["mail"]] if result["mail"] else []

    return result


# ─── PERSISTENCE HELPERS ──────────────────────────────────────────────────────
def load_results() -> list:
    try:
        return json.loads(DATA_FILE.read_text())
    except Exception:
        return []

def save_results(data: list):
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))

def upsert_result(record: dict):
    """Add or update by URL (dedup)."""
    all_data = load_results()
    existing = next((i for i, r in enumerate(all_data) if r.get("url") == record.get("url")), None)
    if existing is not None:
        all_data[existing] = record
    else:
        all_data.append(record)
    save_results(all_data)


# ─── FASTAPI APP ──────────────────────────────────────────────────────────────
app = FastAPI(title="Prospect Research Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── SCHEMAS ──────────────────────────────────────────────────────────────────
class EnrichRequest(BaseModel):
    urls: List[str]
    website_label: Optional[str] = ""   # optional label for record-keeping


class DeleteRequest(BaseModel):
    url: str


# ─── JOB STORE (in-memory for live status) ────────────────────────────────────
jobs: dict = {}   # job_id -> {status, results, errors, total, done}

def _run_enrichment_job(job_id: str, urls: list, label: str):
    jobs[job_id]["status"] = "running"
    for url in urls:
        url = url.strip()
        try:
            data = enrich_company(url)
            record = {
                "id": str(uuid.uuid4()),
                "url": url,
                "label": label,
                "enriched_at": datetime.utcnow().isoformat() + "Z",
                **data,
            }
            upsert_result(record)
            jobs[job_id]["results"].append(record)
        except Exception as e:
            jobs[job_id]["errors"].append({"url": url, "error": str(e)})
        finally:
            jobs[job_id]["done"] += 1

    jobs[job_id]["status"] = "complete"


# ─── ROUTES ───────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.post("/enrich")
def enrich(req: EnrichRequest, background_tasks: BackgroundTasks):
    """
    Accepts multiple URLs, kicks off background enrichment, returns job_id for polling.
    """
    if not req.urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "queued",
        "results": [],
        "errors": [],
        "total": len(req.urls),
        "done": 0,
    }
    background_tasks.add_task(_run_enrichment_job, job_id, req.urls, req.website_label or "")
    return {"job_id": job_id, "total": len(req.urls)}


@app.get("/job/{job_id}")
def job_status(job_id: str):
    """Poll job progress."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/results")
def get_results():
    """Return all enriched companies from file."""
    return load_results()


@app.delete("/results")
def delete_result(req: DeleteRequest):
    """Delete a single result by URL."""
    all_data = load_results()
    filtered = [r for r in all_data if r.get("url") != req.url]
    if len(filtered) == len(all_data):
        raise HTTPException(status_code=404, detail="Record not found.")
    save_results(filtered)
    return {"deleted": req.url, "remaining": len(filtered)}


@app.delete("/results/all")
def delete_all():
    save_results([])
    return {"message": "All results cleared."}
