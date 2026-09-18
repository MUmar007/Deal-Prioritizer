# Deal Prioritizer

A small tool for acquisition search teams. Give it a trade and a city, and it tells you which local businesses are worth enriching or calling first, before you spend list credits on the rest.

## Why

Search funds and small PE shops pull long lead lists out of tools like SaaSquatch, then pay for every row in enrichment credits and calling time. That includes franchise locations nobody is ever going to buy. I wanted something that sits before that step: pick a vertical and a market, get back local operators ranked by how acquirable they look (independent, established, reachable), with chains filtered out and a short reason next to every score. The result exports as a CSV you can drop into a CRM or dialer.

## Features

- Search by vertical and market (with an optional headcount band, see below)
- Live business data from OpenStreetMap via Nominatim and Overpass
- Chains and franchises filtered out using OSM's `brand:wikidata` tags plus a list of known brand names
- 0–100 fit score and tier (prime / solid / watch / reject), based only on what the listing says
- If OpenStreetMap is down, you get sample rows marked as such; they're never cached or exported
- CSV export that matches whatever the filters show on screen
- Swagger docs at `/docs`

## How scoring works

The score only uses what's actually on the OSM listing. A missing tag is worth zero; the tool doesn't guess.

| Signal | Points |
|--------|--------|
| Independent operator (not a chain) | base 50 |
| Phone listed | +12 |
| Email listed | +5 |
| Full street address | +8 |
| Opening hours listed | +5 |
| Website is just a Facebook page or site-builder subdomain (room to modernize) | +8 |
| Own website | +5 |
| Founded 10+ years ago (OSM `start_date`) | +12 |
| Founded 5–9 years ago | +6 |
| Family name in the business ("& Sons", "Bros", "Family") | +10 |
| Chain: `brand:wikidata` tag, known brand name or store number | reject |

Tiers: prime is 78+, solid 58–77, watch 40–57. Listings with no phone, email or website get an "enrich first" note.

OSM doesn't publish staff counts, so the headcount band is saved with each run but doesn't change the score yet (see Roadmap).

## Demo and dataset

- [`demo/api_walkthrough.ipynb`](demo/api_walkthrough.ipynb) walks through the API with real output: a search, the ranked shortlist, chain filtering, CSV export and validation errors.
- [`data/auto-chicago-il.csv`](data/auto-chicago-il.csv) is a real export of 38 Chicago auto repair shops, 7 of which got rejected as chains. More in [`data/README.md`](data/README.md).
- With the stack running, Swagger is at http://localhost:8000/docs.

## Layout

```
deal-prioritizer/
  docker-compose.yml   Postgres, Redis, API and web
  render.yaml          Render blueprint for deploying
  server/              FastAPI API (port 8000)
    app/
      main.py          App factory, CORS, lifespan
      config.py        Settings (env vars / .env)
      db.py            Async engine and session dependency
      models.py        SQLAlchemy models
      schemas.py       Pydantic request/response models
      routers/         HTTP endpoints
      services/        Discovery, ranking, caching
    alembic/           Migrations
    tests/
  web/                 Vite + React UI
    Dockerfile         Builds the UI, serves it with nginx
    nginx.conf         Static files + /v1 proxy to the API
  demo/                Jupyter walkthrough
  data/                Sample dataset (OpenStreetMap, ODbL)
```

## Quick start (Docker)

You need Docker with Compose v2.

```bash
docker compose up --build
```

| Service | URL |
|---------|-----|
| App | http://localhost:3000 |
| API docs (Swagger) | http://localhost:8000/docs |
| API docs (ReDoc) | http://localhost:8000/redoc |
| Health check | http://localhost:8000/healthz |

Migrations run when the API starts. Postgres is exposed on 5433 and Redis on 6380 in case you want to poke at them.

If something else is already on port 3000: `WEB_PORT=3001 docker compose up --build`.

```bash
docker compose logs -f api   # follow API logs
docker compose down          # stop, keep data
docker compose down -v       # stop and wipe the database volume
```

## Local development

For hot reload, run the pieces directly. If the Docker `api` and `web` containers are up, stop them first (`docker compose stop api web`) since they use the same ports.

You'll need Python 3.12, Node 18+ and PostgreSQL 16. Redis is optional.

### 1. PostgreSQL

**Option A: Docker** (just the database and cache):

```bash
docker compose up -d db redis
```

That lines up with the defaults in `server/.env.example` (Postgres on 5433, Redis on 6380).

**Option B: native Postgres** (Homebrew on macOS):

```bash
brew install postgresql@16
brew services start postgresql@16
psql postgres -c "CREATE USER deal WITH PASSWORD 'deal' CREATEDB;"
psql postgres -c "CREATE DATABASE dealprioritizer OWNER deal;"
```

Then set `DATABASE_URL=postgresql+asyncpg://deal:deal@localhost:5432/dealprioritizer` in `server/.env`. Set `REDIS_URL=` (empty) unless you're running Redis yourself; the API then caches in memory.

### 2. API

```bash
cd server
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Docs at http://localhost:8000/docs.

### 3. UI

```bash
cd web
npm install
npm run dev
```

Open whatever URL Vite prints (http://localhost:3000 unless that port is taken). The dev server proxies `/v1` and `/healthz` to the API on 8000.

## Architecture

```mermaid
flowchart LR
    B[Browser] -->|UI + /v1| W[nginx<br/>static React build]
    W -->|proxy /v1| A[FastAPI<br/>async Python]
    A -->|geocode market| N[(Nominatim)]
    A -->|business listings| O[(Overpass API)]
    A <-->|TTL cache| R[(Redis)]
    A <-->|runs + targets| P[(PostgreSQL)]
```

A search is a single request. The API geocodes the market with Nominatim, asks Overpass for matching businesses within about 10 km, drops duplicates, rejects chains, scores what's left and saves the run. After that the UI filters the saved results in the browser, and the CSV export goes back through the API.

### Data storage

PostgreSQL 16 through SQLAlchemy 2.0 (async, asyncpg), with Alembic for migrations. Runs and their companies are plainly relational, and JSONB covers the loose ranking metadata, so Postgres was the obvious fit.

- `buybox_runs`: one row per search (vertical, market, headcount band, `source_status`, timestamp).
- `target_companies`: one row per ranked company, with contact fields, score, tier, rationale, and a JSONB `meta` column for the reject reason and data source.
- UUID primary keys and a cascading foreign key from companies to runs. A unique constraint on `(run_id, dedupe_hash)` backs up the dedupe in code.
- Migrations run on API startup.

### Caching and performance

- Geocodes and discovery results go in Redis for an hour (`CACHE_SECONDS`), or in process memory if Redis isn't configured. A repeat search goes from roughly 13 s to 0.04 s. Keys are versioned, and only live OSM results get cached.
- Everything on the request path is async (FastAPI, httpx, asyncpg, redis.asyncio), so one slow Overpass call doesn't hold up other requests.
- DB connections are pooled with pre-ping, and a run's companies load in one extra query rather than one per row.
- Overpass gets one retry after 2 s when it answers 429 or 5xx. If the geocoder is down the API returns a 503.
- Hashed JS and CSS bundles get a one-year browser cache, while `index.html` is always revalidated so a refresh picks up new builds. Filtering happens in the browser, so no round trips.

### Hosting and deployment

It deploys to [Render](https://render.com), and the whole setup lives in [`render.yaml`](render.yaml).

| Component | Render resource | Hosting model |
|-----------|-----------------|---------------|
| UI | Static site (CDN) | Static |
| API | Web service built from `server/Dockerfile` | Long-running container |
| Database | Managed PostgreSQL 16 | Managed |
| Cache | Managed Key Value (Redis-compatible) | Managed |

The UI is a static build on Render's CDN. The API is a long-running container rather than serverless functions: it holds a DB pool and a Redis client, and Overpass calls can take 10–20 s, which doesn't play well with per-request cold starts. The UI reaches the API through `VITE_API_URL`, and the API allows that origin via `CORS_ORIGINS`.

**Deploying**

1. Push the repo to GitHub.
2. In Render, go to **New → Blueprint** and pick the repo. Render reads `render.yaml` and asks you for `NOMINATIM_UA`, e.g. `DealPrioritizer/1.0 (+https://github.com/<you>/<repo>)`.
3. Render creates the database and Key Value, builds the API image and the static UI, and wires `DATABASE_URL` and `REDIS_URL` into the API.
4. The API runs Alembic migrations each time it starts, then listens on the port Render assigns.
5. After that, every push to the default branch redeploys.

Service names on Render are first come, first served. If you end up with URLs other than `deal-prioritizer-api.onrender.com` / `deal-prioritizer-web.onrender.com`, update `VITE_API_URL` on the web service and `CORS_ORIGINS` on the API, then redeploy.

On the free plan the API sleeps after 15 minutes of no traffic (the first request afterwards takes about a minute), the free database expires after 30 days, and free Key Value isn't persisted. Switch the plans in `render.yaml` to `starter` if you want it always on.

To run it somewhere else, `docker-compose.yml` brings up the same four pieces (nginx UI, API, Postgres, Redis) with one command.

## UX decisions

- Everything is on one screen: search at the top, results below.
- The defaults already give you a shortlist: Min fit 58 shows only solid and prime companies, and chains start hidden.
- Each company shows the reasons behind its score in plain words.
- Tier badges are colour-coded (violet prime, amber solid, grey watch, red reject). Sample rows get a dashed SAMPLE badge.
- Filters apply as you type, and a counter tells you how many rows each one is hiding.
- The CSV always matches what's on screen. Sample rows never make it into an export.
- Failures say what went wrong: a banner when OpenStreetMap is down, a message when nothing matches, and a specific error for bad input or a market that can't be found.
- The headcount fields say "for enrichment", since OSM can't back them up yet.

## Configuration

All API settings come from the environment and none have defaults in code, so the API won't start until every one is set (the error lists whatever is missing).

- Local: copy `server/.env.example` to `server/.env`. It has working values for the Docker database and cache.
- Docker: `docker-compose.yml` sets them for the `api` service.
- Render: `render.yaml` sets them, except `NOMINATIM_UA`, which you enter when creating the blueprint.
- Tests: `tests/conftest.py` sets its own, pointing at the test database.

| Variable | Example | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://deal:deal@localhost:5433/dealprioritizer` | PostgreSQL connection. Plain `postgres://` URLs from Render or Neon work too |
| `REDIS_URL` | `redis://localhost:6380/0` | Redis cache. Leave empty to cache in memory |
| `CACHE_SECONDS` | `3600` | TTL for geocoding and discovery results |
| `HTTP_TIMEOUT` | `15` | Geocoder timeout, seconds |
| `NOMINATIM_UA` | `DealPrioritizer/1.0 (+https://github.com/<you>/<repo>)` | User-Agent for OSM services. Use a real contact; Overpass rejects `example.com` |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | JSON list of browser origins allowed to call the API |

The container's port is separate: it listens on `PORT` if set (Render sets it), otherwise 8000.

The UI has one build-time variable, `VITE_API_URL`, for when it's hosted separately from the API. Leave it unset locally and in Docker, where `/v1` is proxied.

## Tests and linting

The tests hit a real Postgres and create a `dealprioritizer_test` database the first time. If your Postgres is native on 5432, set `TEST_DATABASE_URL=postgresql+asyncpg://deal:deal@localhost:5432/dealprioritizer_test`.

```bash
cd server
source .venv/bin/activate
pytest
ruff check .
ruff format --check .
```

```bash
cd web
npx tsc --noEmit
```

## Migrations

```bash
cd server
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/healthz` | Liveness |
| POST | `/v1/pipeline/runs` | Run a search and rank the results (201) |
| GET | `/v1/pipeline/runs/{id}` | Get a saved run |
| GET | `/v1/pipeline/runs/{id}/export?min_score=0&include_rejects=false` | CSV download (same filters as the UI) |

Bad input or an unknown market comes back as a 422 with FastAPI's `detail`. If the geocoder is down you get a 503. If Overpass is still busy after the retry, the run comes back with `source_status: "unavailable"` and sample companies. Every company has `source: "osm" | "sample"`, and samples are never exported.

## Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 18.3, TypeScript 5.9, Tailwind CSS 3.4, Vite 6.4 |
| Web server | nginx 1.27 (Alpine), built with Node 22 |
| API | Python 3.12, FastAPI 0.141, Pydantic 2.13, pydantic-settings 2.15, uvicorn 0.53 |
| HTTP client | httpx 0.28 (async) |
| Database | PostgreSQL 16, SQLAlchemy 2.0 (asyncio), asyncpg 0.31, Alembic 1.20 |
| Cache | Redis 7, redis-py 8.1 (asyncio) |
| Data sources | OpenStreetMap Nominatim (geocoding) and Overpass API (listings) |
| Quality | pytest 9.1, ruff 0.16 (PEP 8 lint and format) |
| Packaging | Docker, Docker Compose |
| Cloud | Render: static site, Docker web service, managed PostgreSQL, Key Value |

## Data and ethics

- Listings come from [OpenStreetMap](https://www.openstreetmap.org/copyright) through Nominatim and Overpass. © OpenStreetMap contributors, licensed under the ODbL.
- Requests carry an identifying User-Agent, which Nominatim's usage policy asks for. Set `NOMINATIM_UA` with a real contact, such as the repo URL.
- Results are cached so these free public services don't get hammered, and a busy server gets one retry, not a loop.
- Only public listing data is used. There's no scraping behind logins and no CAPTCHA workarounds.
- When OSM is unavailable the fallback rows are marked as samples in both the UI and the API, and they never go into an export.

## Roadmap

- Add an enrichment source (Google Places, for example) for review counts, ratings and whether a business is still open. That's also what would make the headcount band useful.
- Normalize and validate phone numbers and emails so CRM imports come out cleaner.
- Push shortlists straight into a CRM (HubSpot, Salesforce) instead of going through CSV.
- Saved searches and run history in the UI.
