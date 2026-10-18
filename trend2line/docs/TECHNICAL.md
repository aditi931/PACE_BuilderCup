# Trend2Line

Turns a viral food trend into a verified factory decision for a chosen market: can our lines make it, which
existing products would lose their free-from claims, what the label must say in that market, how long to shelf,
how much consumer interest is left when we get there, and which other markets look better. Every number in the
recommendation is checked against recorded evidence.

Built for the Google Cloud AI Builder Cup 2026. Theme: **Manufacturing: Intelligent Operations & Industrial Efficiency**.

## What is real and what is not (read before the demo)

| Input | Status | Source |
|---|---|---|
| Trend interest per market | **Real**, fetched live | Wikimedia pageviews, one series per language edition, linked through Wikidata |
| Fad half-life reference | **Real** once computed (Cloud Build does it; or `python -m scripts.build_fad_reference`). Placeholder until then and verdicts are capped at CONDITIONAL | Wikimedia pageviews of ~29 past fads from several regions (`data/fad_candidates.csv`) |
| Ingredients → allergens | **Real open data** | Open Food Facts ingredient + allergen taxonomies (ODbL), ~5,700 ingredients, many languages |
| Market regulations (12 markets) | **Researched**; each rule shows its source URL and `verified_official`, `verified_secondary` or `needs_review` | `data/markets.yaml` |
| Market size | **Real open data** | World Bank population (2025) and GDP (2023) |
| Place names (origin-claim check) | **Real open data** | GeoNames cities over 15,000 people, ISO country names |
| Plant, lines, SKUs, timeline, equipment lead times, scan weights | **Sample company input**. No public dataset describes a factory; upload your own plant file in the UI | `data/plant.yaml`, `timeline.yaml`, `equipment_catalog.csv`, `scoring.yaml` |
| Product spec and write-up | **Gemini** (canned with `MOCK_LLM=1`) | Gemini interprets; code computes every number, date, allergen and rule |
| `OFFLINE=1` curves | **Synthetic**, for tests and CI only; labelled in the UI | |

`data/manifest.json` records the URL, licence, size, sha256 and fetch time of every downloaded file.
`GET /api/provenance` serves it together with every regulation source.

## Shelf deadline: works without company data

Every run answers "must be on shelf within N days" from public data alone. Interest is assumed to fade as
`0.5 ** (T / half_life)`, so the last day that keeps a share `s` of today's interest is
`T = half_life * log2(1 / s)`, rounded down. With the GO threshold of 50% (`data/timeline.yaml`), the deadline
equals one half-life. It is shown for fast, typical and slow fads (p25, median, p75 half-lives), per market
in the scan, as a red line on the timeline, and as days to spare or days too late against the planned route.
A company with no data in the tool can still compare the deadline with what it knows about its own plant.

## Markets

India, EU, UK, US, Canada, Australia/NZ, Japan, South Korea, China, Singapore, the Gulf states (GCC) and Brazil.
Each has its own allergen list (matched by allergen tag or by ingredient lineage, so Japan's walnut/cashew and
Australia's per-nut names work), effective dates (China 2027, Japan cashew 2026), label languages and rules
(gluten-free statements, origin claims, "may contain"). Add a market by adding a block to `data/markets.yaml`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Git Bash on Windows: source .venv/Scripts/activate
pip install -r requirements-dev.txt
cp .env.example .env                          # the app reads .env itself (MOCK_LLM="1" by default)
python -m scripts.doctor                      # health check with fix hints
pytest -q                                     # offline, no keys needed
uvicorn app.main:app --reload --port 8080     # live trend data, canned Gemini
```

Live Gemini:

```bash
# in .env: set GOOGLE_API_KEY, GEMINI_MODEL and MOCK_LLM="0"
python -m scripts.smoke_gemini     # shows the auth path and lists the models your key can use
python -m scripts.smoke_live       # checks Wikipedia, Wikidata and pageviews
uvicorn app.main:app --reload --port 8080
```

**Key types.** AI Studio keys start with `AIza` and use the Gemini Developer API. Keys starting with `AQ.` are
Vertex AI express-mode keys and must be sent to Vertex (`genai.Client(vertexai=True, api_key=...)`).
`GEMINI_AUTH=auto` detects this; force it with `GEMINI_AUTH=ai_studio|vertex_express|vertex_adc`.
Never commit a key; if a key was ever pasted anywhere public, rotate it.

## Refresh the data

```bash
python -m scripts.refresh_data          # re-download every source in data/sources.yaml, rebuild derived tables
python -m scripts.build_fad_reference   # recompute half-lives from real pageviews (needs Wikimedia access)
```

The Dockerfile runs both during the Cloud Run build and keeps the committed copies if a source is down.

## Deploy to Cloud Run

One-time setup on a personal GCP project with billing enabled:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com
```

First deploy, no key needed: `PROJECT_ID=YOUR_PROJECT_ID MOCK_LLM=1 ./deploy.sh`

Go live with the key in Secret Manager:

```bash
printf '%s' "YOUR_KEY" | gcloud secrets create gemini-api-key --data-file=-
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
PROJECT_ID=YOUR_PROJECT_ID GEMINI_MODEL=YOUR_FLASH_MODEL_ID ./deploy.sh
```

## Your own plant

Download the template from the UI (or `GET /api/plant/template`), edit lines, capabilities and SKUs (ingredients
as plain names), and upload it with the run. Line allergens are computed from the SKU ingredients; add
`extra_allergens` for anything else handled on a line. Uploaded files are validated and never stored.

## Layout

```
app/main.py              FastAPI: /, /healthz, /api/config, /api/run, /api/provenance, /api/plant/*
app/pipeline.py          Stage orchestration, decision rules, market scan, writer/verifier loop
app/stages/ingredients.py  Open Food Facts taxonomy resolver (names, parents, allergens, review flags)
app/stages/trend.py      Wikipedia search, Wikidata sitelinks, pageviews, fad maths (disk-cached)
app/stages/signals.py    Per-market signal and transparent market scoring
app/stages/compliance.py Rules engine over data/markets.yaml
app/stages/              bom, feasibility (allergen blast radius), timeline, verifier, catalog
data/                    Knowledge tables and downloaded sources (authoritative; the LLM never invents these)
scripts/                 refresh_data, build_fad_reference, smoke_gemini, smoke_live, discover_trends_bq
tests/                   pytest: deterministic core, live path against a fake Wikimedia, end-to-end API
```

## Known limitations

- Wikipedia pageviews are a public proxy for attention, not sales. English is shared by many countries, so
  English-only markets are labelled `shared_english` and their interest level is not scored.
- Chinese Wikipedia is not the main platform in mainland China; treat China's interest signal as weak.
- Capture uses `0.5 ** (T / half_life)` relative to interest on the run date.
- Regulation rows marked `needs_review` (notably the Gulf, superseded by GSO 9:2022 and GSO 2810:2025) need a
  regulatory specialist before a real launch. This tool flags what to check; it is not legal advice.
- Composite ingredients can hide allergens; the resolver over-declares from components and flags anything
  approximate, and the verdict is held back until someone reviews it.

See `../ROADMAP.md` for how to take this further.
