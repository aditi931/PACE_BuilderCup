# Trend2Line setup guide: which Google Cloud service is called, and when

This guide takes you from a laptop to a live app on Google Cloud, and explains what each Google service does at
each moment. Read section 1 once; follow sections 3 to 7 in order.

---

## 1. The big picture

There are **four moments** in the life of this app, and different services wake up at each one.

```
 ONE-TIME SETUP           EVERY DEPLOY                 EVERY ANALYSIS (a user clicks "Run")
 ─────────────            ────────────                 ────────────────────────────────────
 Enable APIs              you run ./deploy.sh          browser ──► Cloud Run (the app)
 Store key in      ──►      │                                       │
 Secret Manager             ▼                                       ├──► Wikipedia / Wikidata  (not Google)
 Grant permission         Cloud Build                               ├──► Gemini  (2 to 5 calls)
                            ├─ builds the app                       └──► Cloud Logging (automatic)
                            ├─ refreshes public data
                            └─ computes fad half-lives
                            ▼
                          Artifact Registry  (stores the built app)
                            ▼
                          Cloud Run  (new version goes live;
                                      reads the key from Secret Manager)

 OCCASIONALLY, BY HAND: BigQuery (optional trend discovery script)
```

### Every service at a glance

| Service | Plain-words job | When it is called | How often |
|---|---|---|---|
| **Cloud Run** | Hosts the website and runs each analysis | Every page visit and every "Run analysis" | Per request; sleeps (scales to zero) when idle |
| **Cloud Build** | Packages the code into a container and runs the build steps | Only when you run `./deploy.sh` | Per deploy |
| **Artifact Registry** | Stores the packaged container | Written by Cloud Build at deploy; read by Cloud Run when it starts a new copy | Per deploy and per cold start |
| **Secret Manager** | Holds the Gemini key so it's never in the code | Read when Cloud Run starts a copy of the app | Per cold start, not per request |
| **Gemini** (Vertex AI or AI Studio) | Describes the product and writes the recommendation | During an analysis, live mode only | 2 to 5 calls per analysis (see section 2) |
| **IAM** | Gives Cloud Run permission to read the secret | Checked when Cloud Run reads the secret | Set up once |
| **Cloud Logging** | Keeps the app's logs | Automatically, all the time | Continuous |
| **BigQuery** (optional) | Finds rising searches per country in Google's public Trends dataset | Only when you run `scripts/discover_trends_bq.py` yourself | By hand; the app never calls it |

**Not Google, but called by the app:** Wikipedia search, Wikidata and Wikimedia page views during each analysis;
GitHub-hosted open datasets (Open Food Facts, World Bank, GeoNames) during each deploy.

---

## 2. What happens during one analysis, step by step

| Step | What happens | Service called |
|---|---|---|
| 1 | Browser sends the trend, SKU, date and market | **Cloud Run** receives it (starts a copy first if it was asleep: a "cold start") |
| 2 | Find the Wikipedia article | Wikipedia search (1 request, more if not found in English) |
| 3 | If several articles match, pick the right one | **Gemini**, 1 call, only when there's more than one candidate |
| 4 | Find the same article in other languages | Wikidata (1 to 2 requests) |
| 5 | Download daily views in up to 18 languages | Wikimedia page views (up to 19 requests, run in parallel) |
| 6 | Describe the product: ingredients and steps | **Gemini**, 1 call (includes the photo if uploaded) |
| 7 | Allergens, lines, timeline, deadline, labelling rules, market scan | No external call: pure code over the files in `data/` |
| 8 | Write the recommendation | **Gemini**, 1 call |
| 9 | Fact-check; if it fails, ask Gemini to fix it | **Gemini**, up to 2 more calls (`MAX_WRITER_ATTEMPTS=3`) |
| 10 | Send the result to the browser; log the run | **Cloud Run**, **Cloud Logging** |

**Totals per analysis:** 2 to 5 Gemini calls, 3 to about 25 Wikimedia requests.

**Caching saves calls.** Wikimedia answers are kept for 12 hours (`HTTP_CACHE_HOURS`), and identical Gemini
requests are reused, on the same running copy of the app. Running the same trend twice is fast and free the
second time, until that copy is shut down.

**In `MOCK_LLM=1` mode,** steps 3, 6, 8 and 9 use canned answers and Gemini is never called. Wikipedia data is
still live.

---

## 3. Before you start (one time, about 15 minutes)

You need:

- a **personal Google account** and a **Google Cloud project with billing enabled**
- the **gcloud CLI** installed (`gcloud --version` to check)
- **Python 3.12**
- a **Gemini key**, either:
  - an **AI Studio key** (starts with `AIza`), or
  - a **Vertex AI express-mode key** (starts with `AQ.`)

  The app detects which one you have automatically.

> **Security:** never paste a key into chat, code, a screenshot or Git. If that has happened, delete the key and
> create a new one before going further.

**Set a budget alert** first. In the Cloud console go to *Billing → Budgets & alerts*, and create a small budget
with email alerts. This is the simplest protection against surprises.

---

## 4. Run it on your laptop first

Run everything **from the repo folder** (the one containing `app/`, `data/` and `deploy.sh`). The app reads `.env`
by itself, so the same steps work in every terminal; you never need `source .env` or `VAR=1 command` tricks.

### 4a. Create the environment (once)

| Step | Git Bash (Windows) | PowerShell (Windows) | Mac / Linux |
|---|---|---|---|
| Create | `python -m venv .venv` (or `py -m venv .venv`) | `python -m venv .venv` | `python3 -m venv .venv` |
| Activate (every new terminal) | `source .venv/Scripts/activate` | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| Install | `pip install -r requirements-dev.txt` | same | same |
| Settings | `cp .env.example .env` | `Copy-Item .env.example .env` | `cp .env.example .env` |

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

### 4b. Health check (do this first, and whenever something breaks)

```bash
python -m scripts.doctor
```

It checks Python, packages, `.env`, your key type, data files, line endings, an offline end-to-end run and network
access to Wikipedia, Wikidata and Gemini, and prints a fix next to every FAIL. Paste its output when asking for help.

### 4c. Tests (no keys, no internet)

```bash
pytest -q          # should say 32 passed
```

### 4d. Run the app

```bash
uvicorn app.main:app --reload --port 8080
```

Open http://localhost:8080. Which mode you get depends on `.env`:

| `.env` | Mode | What you see |
|---|---|---|
| `MOCK_LLM="1"` (the default in `.env.example`) | Live trend data, canned Gemini | Banner "MOCK GEMINI" |
| `MOCK_LLM="0"`, `GOOGLE_API_KEY` and `GEMINI_MODEL` set | Fully live | No banner |
| `OFFLINE="1"` | No internet at all, synthetic curves | Banner "OFFLINE MODE" (tests only) |

With no `.env` and no key at all, the app falls back to mock Gemini rather than failing.

### 4e. Go fully live

1. Put your key in `.env` as `GOOGLE_API_KEY="..."` and set `MOCK_LLM="0"`.
2. Run `python -m scripts.smoke_gemini`. It shows the auth path and lists the models your key can use.
3. Put one Flash model into `GEMINI_MODEL="..."` in `.env`.
4. Run `python -m scripts.doctor --gemini`. It should end with 0 FAIL.
5. Run `python -m scripts.build_fad_reference` to compute real fad half-lives (a few minutes).
6. Start the app again.

### 4f. If Windows shows strange "modified" files or `$'\r': command not found`

That's Windows line endings. The repo's `.gitattributes` prevents it for new checkouts. To fix an existing checkout:

```bash
git add --renormalize . && git status
sed -i 's/\r$//' deploy.sh scripts/*.sh Dockerfile
```

---

## 5. One-time Google Cloud setup

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Switch on the services the app needs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

# Only if your key starts with "AQ." (Vertex AI express mode) and was created in this project
gcloud services enable aiplatform.googleapis.com
```

What each command switches on:

- **`run`** is Cloud Run, which hosts the app.
- **`cloudbuild`** is Cloud Build, which builds it.
- **`artifactregistry`** stores the build.
- **`secretmanager`** holds the key.
- **`aiplatform`** is Vertex AI, which serves Gemini for `AQ.` keys.

### Store the key in Secret Manager

```bash
printf '%s' "PASTE_YOUR_NEW_KEY_HERE" | gcloud secrets create gemini-api-key --data-file=-
```

Using `printf` with a pipe keeps the key out of files. Clear your terminal history afterwards if you're worried.

### Let Cloud Run read the secret (IAM)

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Cloud Run runs as this "compute" service account, which needs permission to open the secret.

---

## 6. Deploy

### 6a. First deploy, with no key (canned AI, live data)

```bash
PROJECT_ID=YOUR_PROJECT_ID MOCK_LLM=1 ./deploy.sh
```

What happens behind the scenes:

1. `deploy.sh` checks that no non-Google AI library has crept in.
2. **Cloud Build** receives the code and follows the `Dockerfile`:
   1. Installs Python packages.
   2. Downloads fresh copies of the open datasets (Open Food Facts, World Bank, GeoNames, country codes).
   3. Computes fad half-lives from real Wikipedia page views.
   4. If any download fails, keeps the copies already in `data/` and carries on.
3. **Artifact Registry** stores the finished container.
4. **Cloud Run** starts it in `asia-south1` (Mumbai) and prints a public URL.

The first build takes several minutes; later ones are faster.

### 6b. Go live with Gemini

```bash
PROJECT_ID=YOUR_PROJECT_ID GEMINI_MODEL=YOUR_FLASH_MODEL_ID ./deploy.sh
```

This also tells **Cloud Run** to load `GOOGLE_API_KEY` from **Secret Manager** each time it starts a copy.

### 6c. Check it worked

```bash
URL=$(gcloud run services describe trend2line --region asia-south1 --format='value(status.url)')
curl "$URL/healthz"                    # {"ok":true}
curl "$URL/api/config"                 # mode should be "live"; reference_status should be "computed"
```

Then open the URL in a browser and run one analysis.

If `reference_status` isn't `computed`, Cloud Build couldn't reach Wikimedia or found too few fads. The app still
works, but verdicts stay capped at CONDITIONAL. Run `python -m scripts.build_fad_reference` on your laptop and
deploy again. The new `data/fad_reference.json` is uploaded with the code, and a later build that can't reach
Wikimedia keeps it rather than overwriting it.

---

## 7. Day to day

| You want to… | Do this | Services involved |
|---|---|---|
| Ship a code or data change | `./deploy.sh` again (same variables as before) | Cloud Build, Artifact Registry, Cloud Run |
| Change the key | `printf '%s' "NEW_KEY" \| gcloud secrets versions add gemini-api-key --data-file=-`, then redeploy | Secret Manager, Cloud Run |
| See errors | `gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="trend2line"' --limit 50`, or open Cloud Run → trend2line → Logs in the console | Cloud Logging |
| Avoid slow first load on demo day | Add `--min-instances 1` to `ARGS` in `deploy.sh` and redeploy. One copy stays warm and costs a little while it does | Cloud Run |
| Remove the warm copy afterwards | Set `--min-instances 0` and redeploy | Cloud Run |
| Discover new rising trends | `python -m scripts.discover_trends_bq --project YOUR_PROJECT_ID` (needs `pip install google-cloud-bigquery`) | BigQuery |
| Shut everything down | `gcloud run services delete trend2line --region asia-south1` | Cloud Run |

---

## 8. Settings (environment variables)

| Variable | What it controls | Typical value |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini key (comes from Secret Manager in the cloud) | set by `deploy.sh` |
| `GEMINI_MODEL` | Which Gemini model to use | a Flash model ID from `smoke_gemini` |
| `GEMINI_AUTH` | How to reach Gemini: `auto`, `ai_studio`, `vertex_express`, `vertex_adc` | `auto` |
| `MOCK_LLM` | `1` = canned AI answers, live data | `0` in production |
| `OFFLINE` | `1` = no internet at all, synthetic data (tests only) | `0` |
| `WIKI_USER_AGENT` | Identifies you to Wikimedia; include a contact email | `Trend2Line/0.2 (you@example.com)` |
| `HTTP_CACHE_HOURS` | How long Wikimedia answers are reused | `12` |
| `MAX_WRITER_ATTEMPTS` | How many times the writer may retry after the fact-checker rejects it | `3` |

Cloud Run settings in `deploy.sh`:
- region `asia-south1`
- 1 CPU and 1 GiB memory
- 300-second timeout
- at most 3 copies running
- public access (`--allow-unauthenticated`)

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Deploy fails with a permissions error | Cloud Build service account lacks a role | Copy the exact error; it usually names the missing role to grant |
| Page shows "GEMINI_MODEL is not set" | Live mode without a model | Redeploy with `GEMINI_MODEL=...` |
| 503 error mentioning Gemini, or 401 "API keys are not supported" | Key sent to the wrong endpoint, or Vertex AI not enabled | Run `smoke_gemini` locally; for `AQ.` keys enable `aiplatform.googleapis.com`; set `GEMINI_AUTH` explicitly |
| "No Wikipedia article found" | The trend has no article yet | Try another spelling or the original-language name; very new trends may not be on Wikipedia |
| Banner says the half-life reference is a placeholder | Fad reference not computed | See section 6c |
| First request after a quiet period is slow | Cold start | Normal; use `--min-instances 1` on demo day |

---

## 10. Cost notes

The app is light. Cloud Run charges mainly while a request is being handled, and scales to zero when idle unless
you set `--min-instances`. Gemini is charged per call: 2 to 5 per analysis, Flash models being the cheapest. Cloud
Build charges for build minutes during deploys. Wikimedia and the open datasets are free.

Check current prices and free tiers on Google's pricing pages; they change. Keep the budget alert from section 3
switched on.
