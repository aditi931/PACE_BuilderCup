"""Health check: run from the repo root with   python -m scripts.doctor
Add --gemini to also make one tiny Gemini call, and --gcp to exercise every Google Cloud service for real
(Vertex AI models and grounding, BigQuery dry run, Firestore write/read/delete).

Prints OK / WARN / FAIL for each check with a fix hint, then a summary. Paste the output when asking for help.
It never prints your key, only its type and length.
"""
import argparse
import datetime as dt
import importlib
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS: list[tuple[str, str]] = []


def report(status: str, name: str, detail: str = "", fix: str = "") -> None:
    RESULTS.append((status, name))
    print(f"[{status:4}] {name}" + (f": {detail}" if detail else ""))
    if fix and status != "OK":
        print(f"       fix: {fix}")


def check_python() -> None:
    v = sys.version_info
    if v < (3, 10):
        report("FAIL", "Python version", sys.version.split()[0], "install Python 3.12 and recreate the .venv")
    elif v < (3, 12):
        report("WARN", "Python version", f"{sys.version.split()[0]} (3.12 recommended, same as Docker)")
    else:
        report("OK", "Python version", sys.version.split()[0])
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    report("OK" if in_venv else "WARN", "Virtual environment", sys.prefix,
           "activate it: Git Bash `source .venv/Scripts/activate`, PowerShell `.venv\\Scripts\\Activate.ps1`, "
           "Mac/Linux `source .venv/bin/activate`")


def check_packages() -> None:
    for mod, pip_name in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("pydantic", "pydantic"),
                          ("httpx", "httpx"), ("yaml", "pyyaml"), ("google.genai", "google-genai"),
                          ("multipart", "python-multipart")]:
        try:
            m = importlib.import_module(mod)
            report("OK", f"package {pip_name}", getattr(m, "__version__", "installed"))
        except Exception as exc:
            report("FAIL", f"package {pip_name}", str(exc), "pip install -r requirements-dev.txt")


def check_repo() -> Path:
    root = Path.cwd()
    if not (root / "app").is_dir() or not (root / "data").is_dir():
        report("FAIL", "Working folder", str(root), "cd into the repo folder (the one with app/, data/, deploy.sh)")
        sys.exit(1)
    report("OK", "Working folder", str(root))
    for f in ["deploy.sh", "Dockerfile", "scripts/check_gemini_only.sh"]:
        p = root / f
        if p.exists() and b"\r\n" in p.read_bytes():
            report("FAIL", f"Line endings {f}", "Windows (CRLF); bash/Docker will fail",
                   "run: sed -i 's/\\r$//' deploy.sh scripts/*.sh Dockerfile   (the .gitattributes file prevents it recurring)")
        elif p.exists():
            report("OK", f"Line endings {f}", "LF")
    return root


def check_env(root: Path):
    from app.config import DOTENV_LOADED, get_settings
    env_file = root / ".env"
    if env_file.exists():
        report("OK", ".env file", f"loaded {len(DOTENV_LOADED)} settings: {', '.join(DOTENV_LOADED) or '(all already set)'}")
    else:
        report("WARN", ".env file", "not found (fine for offline/mock)", "cp .env.example .env  and fill it in")
    s = get_settings()
    mode = "offline" if s.offline else ("mock_llm" if s.mock_llm else "live")
    report("OK", "Mode", mode)
    key = s.google_api_key
    if key:
        kind = "Vertex express (AQ.)" if key.startswith("AQ.") else "AI Studio (AIza)" if key.startswith("AIza") else "unknown type"
        report("OK" if kind != "unknown type" else "WARN", "Gemini key", f"{kind}, {len(key)} characters")
    elif mode == "live":
        report("FAIL", "Gemini key", "missing in live mode", "set GOOGLE_API_KEY in .env, or MOCK_LLM=\"1\" to demo without it")
    report("OK", "Gemini auth path", s.resolved_auth)
    if mode == "live" and not s.gemini_model:
        report("FAIL", "GEMINI_MODEL", "not set", "python -m scripts.smoke_gemini lists models; put one in .env")
    elif s.gemini_model:
        report("OK", "GEMINI_MODEL", s.gemini_model)
    return s


def check_data() -> None:
    import json
    import yaml
    from app.config import DATA_DIR
    srcs = yaml.safe_load((DATA_DIR / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    for s in srcs:
        p = DATA_DIR / s["file"]
        ok = p.exists() and p.stat().st_size > 1000
        report("OK" if ok else "FAIL", f"data {s['file']}", f"{p.stat().st_size:,} bytes" if p.exists() else "missing",
               "python -m scripts.refresh_data")
    try:
        t = time.time()
        from app.stages.ingredients import taxonomy
        tx = taxonomy()
        report("OK", "Ingredient taxonomy", f"{len(tx.nodes):,} ingredients in {time.time() - t:.1f}s")
    except Exception as exc:
        report("FAIL", "Ingredient taxonomy", str(exc), "python -m scripts.refresh_data")
    try:
        from app.stages import catalog
        report("OK", "Markets", f"{len(catalog.markets()['markets'])} markets")
        report("OK", "Plant", f"{len(catalog.default_plant()['lines'])} lines, {len(catalog.default_plant()['skus'])} SKUs")
    except Exception as exc:
        report("FAIL", "Markets/plant files", str(exc), "check data/markets.yaml and data/plant.yaml for edits")
    ref = json.loads((DATA_DIR / "fad_reference.json").read_text(encoding="utf-8"))
    st = ref.get("status")
    report("OK" if st == "computed" else "WARN", "Fad half-life reference", st,
           "python -m scripts.build_fad_reference (needs internet); until then verdicts cap at CONDITIONAL")


def check_offline_run() -> None:
    from dataclasses import replace
    from app import pipeline
    from app.config import get_settings
    from app.llm import LLM
    from app.mock import MOCK_HANDLERS
    s = replace(get_settings(), offline=True, mock_llm=True, cache_dir=Path(".doctor_cache"))
    try:
        r = pipeline.run("Dubai chocolate", next(iter(__import__("app.stages.catalog", fromlist=["x"]).default_plant()["skus"])),
                         dt.date(2025, 3, 1), "GCC", None, LLM(s, MOCK_HANDLERS), s, scan=True)
        report("OK", "Offline end-to-end run", f"{r['decision']['verdict']}, {len(r['market_scan'])} markets scanned")
    except Exception as exc:
        report("FAIL", "Offline end-to-end run", repr(exc), "paste this output to your coding assistant")


def check_network(s) -> None:
    import httpx
    ua = {"User-Agent": s.wiki_user_agent}
    targets = [
        ("Wikipedia search", "https://en.wikipedia.org/w/api.php",
         {"action": "opensearch", "search": "Dalgona coffee", "limit": 1, "format": "json"}),
        ("Wikidata", "https://www.wikidata.org/w/api.php",
         {"action": "wbgetentities", "ids": "Q90", "props": "info", "format": "json"}),
        ("Wikimedia pageviews", "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/"
                                "all-access/user/Dalgona_coffee/daily/20200301/20200307", None),
        ("GitHub (open datasets)", "https://raw.githubusercontent.com/datasets/country-codes/main/README.md", None),
        ("Gemini AI Studio endpoint", "https://generativelanguage.googleapis.com/", None),
        ("Vertex AI endpoint", "https://aiplatform.googleapis.com/", None),
    ]
    for name, url, params in targets:
        try:
            r = httpx.get(url, params=params, headers=ua, timeout=10, follow_redirects=True)
            reachable = "googleapis" in url or r.status_code == 200
            report("OK" if reachable else "FAIL", f"Network: {name}", f"HTTP {r.status_code}",
                   "Wikimedia blocks requests without a proper WIKI_USER_AGENT; check proxy/firewall")
        except Exception as exc:
            report("FAIL", f"Network: {name}", type(exc).__name__,
                   "no internet, a proxy, or a firewall; on a corporate network try a hotspot or Cloud Shell")


def check_gemini(s) -> None:
    from pydantic import BaseModel
    from app.llm import LLM

    class Ping(BaseModel):
        ok: bool

    try:
        r = LLM(s).structured("doctor", "Reply with ok=true.", Ping)
        report("OK", "Gemini call", f"{s.gemini_model} -> {r.ok}")
    except Exception as exc:
        report("FAIL", "Gemini call", str(exc)[:300],
               "AQ. keys need GEMINI_AUTH=vertex_express (auto does this) and the Vertex AI API enabled; "
               "AIza keys need GEMINI_AUTH=ai_studio. Check GEMINI_MODEL with scripts.smoke_gemini")


def check_gcp(s) -> None:
    """Real calls to each Google Cloud service the app uses. Costs: a few Gemini tokens, a free dry run."""
    hint_auth = "gcloud auth application-default login  and set GOOGLE_CLOUD_PROJECT in .env"
    try:
        import google.auth
        creds, proj = google.auth.default()
        report("OK", "Google credentials (ADC)", f"project from ADC: {proj or '-'}; GOOGLE_CLOUD_PROJECT: {s.gcp_project or '-'}")
    except Exception as exc:
        report("FAIL", "Google credentials (ADC)", type(exc).__name__, hint_auth)
        return
    if not s.gcp_project:
        report("FAIL", "GOOGLE_CLOUD_PROJECT", "not set", "set it in .env (deploy.sh sets it on Cloud Run)")
        return
    from app.llm import LLM
    llm = LLM(s)
    try:
        names = llm.list_models()
        flash = [n for n in names if "flash" in n]
        report("OK" if names else "WARN", f"Gemini models on {s.resolved_auth}", ", ".join(flash[:6]) or "none listed",
               "enable aiplatform.googleapis.com and grant roles/aiplatform.user")
    except Exception as exc:
        report("FAIL", "Gemini model list", str(exc)[:200], "gcloud services enable aiplatform.googleapis.com")
    if s.gemini_model:
        try:
            text, sources = llm.grounded("doctor", "Which city is the capital of France? Answer in one word.")
            report("OK" if sources else "WARN", "Gemini + Google Search grounding",
                   f"answer '{text.strip()[:30]}', {len(sources)} web sources",
                   "grounding may be unavailable for this model or region; try GOOGLE_CLOUD_LOCATION=global")
        except Exception as exc:
            report("FAIL", "Gemini + Google Search grounding", str(exc)[:200], "check GEMINI_MODEL and Vertex AI access")
    try:
        from google.cloud import bigquery
        from app.stages import radar
        client = bigquery.Client(project=s.gcp_project)
        cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, query_parameters=[
            bigquery.ScalarQueryParameter("days", "INT64", 14),
            bigquery.ArrayQueryParameter("codes", "STRING", ["IN", "JP"]),
            bigquery.ScalarQueryParameter("include_us", "BOOL", True)])
        job = client.query(radar.SQL, job_config=cfg)
        gb = (job.total_bytes_processed or 0) / 1024 ** 3
        report("OK" if gb <= s.bq_max_gb else "WARN", "BigQuery Google Trends (dry run)",
               f"radar query would scan {gb:.2f} GB (cap {s.bq_max_gb} GB)", "raise BQ_MAX_GB or lower the days")
    except Exception as exc:
        report("FAIL", "BigQuery", str(exc)[:200], "gcloud services enable bigquery.googleapis.com; grant roles/bigquery.jobUser")
    try:
        from google.cloud import firestore
        db = firestore.Client(project=s.gcp_project)
        ref = db.collection("trend2line_doctor").document("ping")
        ref.set({"at": dt.datetime.now(dt.timezone.utc).isoformat()})
        ok = ref.get().exists
        ref.delete()
        report("OK" if ok else "FAIL", "Firestore write/read/delete", "ok" if ok else "read back failed")
    except Exception as exc:
        report("FAIL", "Firestore", str(exc)[:200],
               "gcloud firestore databases create --location=asia-south1; grant roles/datastore.user")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gemini", action="store_true", help="also make one tiny Gemini call")
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--gcp", action="store_true", help="exercise Vertex AI, grounding, BigQuery and Firestore")
    a = ap.parse_args()
    print(f"Trend2Line doctor  {dt.datetime.now():%Y-%m-%d %H:%M}  on {sys.platform}\n")
    check_python()
    check_packages()
    if any(st == "FAIL" for st, _ in RESULTS):
        print("\nFix the package problems above first.")
        sys.exit(1)
    root = check_repo()
    s = check_env(root)
    check_data()
    check_offline_run()
    if not a.no_network:
        check_network(s)
    if a.gemini and not s.mock_llm:
        check_gemini(s)
    if a.gcp:
        check_gcp(s)
    fails = [n for st, n in RESULTS if st == "FAIL"]
    warns = [n for st, n in RESULTS if st == "WARN"]
    print(f"\n{len(RESULTS) - len(fails) - len(warns)} OK, {len(warns)} WARN, {len(fails)} FAIL")
    if fails:
        print("Fix the FAIL lines first, top to bottom.")
        sys.exit(1)


if __name__ == "__main__":
    main()
