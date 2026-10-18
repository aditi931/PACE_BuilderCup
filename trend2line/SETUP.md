# Trend2Line setup: the Google Cloud services, when each is called, and how to deploy

Trend2Line runs live on Google Cloud. Gemini runs on **Vertex AI** through the Cloud Run service account (no API key
needed), finds real launches with **Google Search grounding**, scans rising searches with **BigQuery**, and stores every
decision in **Firestore**. Mock mode exists only for the automated tests.

---

## 1. The services and when each one is called

| Service | What it does here | When it is called |
|---|---|---|
| **Cloud Run** | Hosts the web app and runs each analysis | Every page load and every "Run analysis" |
| **Vertex AI: Gemini** | Picks the Wikipedia article, describes the product (from text or a photo), writes the summary that the fact-checker verifies | 2 to 5 calls per analysis |
| **Vertex AI: Google Search grounding** | Finds real company launches of the trend on the live web, with source links; code validates every row | 1 call per analysis (cached) |
| **BigQuery** (public Google Trends data) | Trend radar: top rising searches per market over the last N days | When someone clicks "Scan last 14 days" (cached 6 hours; hard cap on bytes billed) |
| **Vertex AI: Gemini** (radar) | Labels which rising searches are food trends and translates them | Once per radar scan |
| **Firestore** | Saves every decision with its full evidence; powers "Recent decisions"; keeps the latest opportunity watch | After every analysis, when history is opened, after each watch |
| **Cloud Scheduler** | Calls `POST /api/watch/run` daily (header `X-Watch-Token`): rising food trends run against every company | Once a day, 06:00 Asia/Kolkata (set by `deploy.sh`; `WATCH_SCHEDULE`, `WATCH_TZ`) |
| **Vertex AI: Gemini** (assistants) | Drafts the label in the market's language; answers "Ask Trend2Line" questions. Code checks both | Only when a user clicks *Draft label* or asks a question |
| **Cloud Build** | Builds the container and, during the build, refreshes the open datasets, computes the fad half-lives and builds the real company profiles | Every `./deploy.sh` |
| **Artifact Registry** | Stores the built container | Every deploy and every cold start |
| **Secret Manager** | Holds a Gemini API key, only if you choose `AUTH=api_key` | When Cloud Run starts a copy |
| **IAM** | Lets the Cloud Run service account use Vertex AI, Firestore and BigQuery | Set up once by `deploy.sh` |
| **Cloud Logging** | App logs | Continuously |

Not Google, but called live: **Wikipedia, Wikidata and Wikimedia page views** (interest per market, every
analysis), **Open Food Facts** (company profiles, at build time) and GitHub-hosted open datasets (at build time).

### One analysis, step by step

| Step | What happens | Service |
|---|---|---|
| 1 | Find the trend's Wikipedia article (Gemini picks if several match) | Wikipedia, **Vertex AI** |
| 2 | The same article in every language, then daily views per market language | Wikidata, Wikimedia |
| 3 | Describe the product: ingredients and process steps | **Vertex AI** |
| 4 | Find real launches of this trend on the web, with sources | **Vertex AI + Google Search** |
| 5 | Allergens, lines, labelling rules, trend windows, real time to market, deadline, market scan | Code over `data/` |
| 6 | Write the summary; the fact-checker sends it back if any number can't be traced | **Vertex AI** (1 to 3 calls) |
| 7 | Save the decision with its evidence | **Firestore** |

---

## 2. Deploy (one command)

**Prerequisites:**
- a Google Cloud project with billing enabled
- the gcloud CLI, signed in with `gcloud auth login`
- a budget alert under *Billing → Budgets & alerts*

Find a Gemini model ID your project can use:

```bash
gcloud auth application-default login
GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID python -m scripts.smoke_gemini     # lists Gemini models on Vertex AI
```

Deploy:

```bash
PROJECT_ID=YOUR_PROJECT_ID GEMINI_MODEL=YOUR_FLASH_MODEL_ID ./deploy.sh
```

`deploy.sh` is safe to re-run. It does the following:

1. Enables Cloud Run, Cloud Build, Artifact Registry, Secret Manager, Vertex AI, Firestore and BigQuery.
2. Creates the Firestore database if it doesn't exist.
3. Grants the Cloud Run service account `aiplatform.user`, `datastore.user`, `bigquery.jobUser` and `logging.logWriter`.
4. Builds with Cloud Build. The build refreshes the open data, computes the fad reference and builds the company profiles.
5. Deploys to Cloud Run in **live mode**, with Gemini on Vertex AI through the service account. No API key is needed.
6. Calls `/healthz` and `/api/config`, and prints which services are live.

The final line should look like this:

```
mode live | gemini Vertex AI vertex_adc <model> | grounding True | bigquery True | runs firestore | fad reference computed | companies 4
```

**Options:**

| Variable | Default | Meaning |
|---|---|---|
| `SERVICE` | `trend2line` | Use `trend2line-dev` for a test copy |
| `REGION` | `asia-south1` | Cloud Run region |
| `GEMINI_LOCATION` | `global` | Vertex AI location for Gemini |
| `FIRESTORE_LOCATION` | `asia-south1` | Only used when creating the database |
| `AUTH` | `vertex_sa` | `api_key` uses the `gemini-api-key` secret instead of the service account |
| `SETUP` | `1` | `0` skips APIs, Firestore and IAM for faster redeploys |
| `MIN_INSTANCES` | `0` | Set `1` on demo day to avoid slow first loads (small cost) |

**Using an API key instead** (for example a Vertex express-mode `AQ.` key):

```bash
printf '%s' "YOUR_NEW_KEY" | gcloud secrets create gemini-api-key --data-file=-
AUTH=api_key PROJECT_ID=YOUR_PROJECT_ID GEMINI_MODEL=YOUR_FLASH_MODEL_ID ./deploy.sh
```

Never paste a key into chat, code or Git. If that has happened, delete it and create a new one.

---

## 3. Run on your laptop against the same Google Cloud services

Run everything from the repo folder. The app reads `.env` by itself.

| Step | Git Bash (Windows) | PowerShell | Mac / Linux |
|---|---|---|---|
| Create env | `python -m venv .venv` | `python -m venv .venv` | `python3 -m venv .venv` |
| Activate | `source .venv/Scripts/activate` | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| Install | `pip install -r requirements-dev.txt` | same | same |
| Settings | `cp .env.example .env` | `Copy-Item .env.example .env` | `cp .env.example .env` |

1. In `.env`, set `GOOGLE_CLOUD_PROJECT` and `GEMINI_MODEL`, and leave `MOCK_LLM="0"`.
2. Sign in once for Vertex AI, BigQuery and Firestore:

   ```bash
   gcloud auth application-default login
   ```

3. Check every service for real, then start the app:

   ```bash
   python -m scripts.doctor --gcp
   uvicorn app.main:app --reload --port 8080
   ```

What to expect:
- **The doctor** lists the Gemini models, makes a grounded search, does a free BigQuery dry run (showing the bytes the radar would scan), and writes, reads and deletes a Firestore document. Each FAIL line comes with its fix.
- **Run history** goes to local files on a laptop. Set `RUN_STORE="firestore"` to use Firestore locally too.
- **If PowerShell blocks `Activate.ps1`**, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### Real data builds (needs internet)

```bash
python -m scripts.build_fad_reference                  # fad half-lives from real page views
python -m scripts.build_company_profile --all          # Lindt, Cadbury India, Amul, Britannia
python -m scripts.case_study --trend "Dubai chocolate" --market IN --company cadbury   # report in reports/
```

### Automated tests (no cloud, no internet)

```bash
pytest -q          # 47 passed; the tests use fakes shaped like the real Google responses
```

---

## 4. Day to day

| You want to… | Do this |
|---|---|
| Ship a change | `SETUP=0 PROJECT_ID=… GEMINI_MODEL=… ./deploy.sh` |
| Check the live services | Open the app: the strip under the title shows each service as live or off |
| See saved decisions | "Recent decisions" in the app, or the `trend2line_runs` collection in the Firestore console |
| See errors | Cloud Run → trend2line → Logs, or `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="trend2line"' --limit 50` |
| Demo day | `MIN_INSTANCES=1` in the deploy command, then set it back to `0` afterwards |
| Shut down | `gcloud run services delete trend2line --region asia-south1` |

---

## 5. Settings

| Variable | Meaning | Cloud Run value (set by deploy.sh) |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Project for Vertex AI, BigQuery and Firestore | your project |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI location | `global` |
| `GEMINI_MODEL` | Gemini model ID | from `smoke_gemini` |
| `GEMINI_AUTH` | `vertex_adc` (service account), `vertex_express`, `ai_studio`, `auto` | `vertex_adc` |
| `ENABLE_GROUNDING` | Google Search grounding for launch discovery | `1` |
| `ENABLE_BIGQUERY` / `BQ_MAX_GB` | Trend radar and its cost cap | `1` / `20` |
| `RUN_STORE` | `firestore`, `local`, `off` (auto picks Firestore on Cloud Run) | `firestore` |
| `MOCK_LLM`, `OFFLINE` | Test switches only | `0`, `0` |
| `WIKI_USER_AGENT` | Identifies you to Wikimedia and Open Food Facts; must contain a URL or email or Wikimedia answers 403 | the service URL is added automatically when missing |
| `WATCH_TOKEN` | Shared secret for `POST /api/watch/run`; without it the endpoint is closed on Cloud Run | random, rotated by every `deploy.sh` (the scheduler job is updated to match) |

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `PERMISSION_DENIED` from Vertex AI | Missing role, or API off | Re-run `deploy.sh` with `SETUP=1`; locally run `gcloud auth application-default login` |
| Model not found | Wrong ID, or model not offered in `GEMINI_LOCATION` | `python -m scripts.smoke_gemini`; keep `GEMINI_LOCATION=global` |
| Grounding returns no sources | Model or region doesn't support it | `python -m scripts.doctor --gcp` shows it; launches then come only from the curated list |
| Radar says BigQuery failed | API off, missing `bigquery.jobUser`, or the query exceeds `BQ_MAX_GB` | Re-run with `SETUP=1`; `doctor --gcp` shows the bytes the query would scan |
| "Run history unavailable" | No Firestore database, or missing `datastore.user` | Re-run with `SETUP=1` |
| Build fails on permissions | Cloud Build account lacks a role | The error names the role; grant it with `gcloud projects add-iam-policy-binding` |
| `$'\r': command not found` | Windows line endings | `sed -i 's/\r$//' deploy.sh scripts/*.sh Dockerfile` |
| First request is slow | Cold start | `MIN_INSTANCES=1` on demo day |
| Opportunity watch says "No watch run yet" | The scheduler job has not fired yet | `gcloud scheduler jobs run <service>-watch --location <region>` |
| Watch run answers 403 | Token mismatch (e.g. a manual deploy without `deploy.sh`) | Re-run `deploy.sh`; it sets `WATCH_TOKEN` and updates the job |

---

## 7. Cost notes

- **Cloud Run** charges mainly while handling requests, and scales to zero when idle.
- **Gemini** is billed per call: about 3 to 6 per analysis, plus one grounded search. Results are cached.
- **BigQuery** is capped per query by `BQ_MAX_GB`, and the radar is cached for 6 hours. `doctor --gcp` shows the scan size up front.
- **Firestore** writes one document per analysis, plus one per watch run.
- **Opportunity watch**: up to 30 quick runs a day (no write-up, no grounded search); Gemini is called only for new
  trends' product specs and the radar labels, and those are cached.

Check Google's current pricing pages, and keep a budget alert on.
