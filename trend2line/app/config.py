"""Runtime settings, read from environment variables only."""
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> list[str]:
    """Read KEY=VALUE lines from a local .env into os.environ, without overriding real environment variables.

    Lets the same setup work in Git Bash, PowerShell, cmd, macOS and Linux (no `source .env` needed).
    Cloud Run never sees a .env (excluded by .gcloudignore/.dockerignore); there, settings come from deploy.sh.
    """
    loaded: list[str] = []
    if not path.is_file():
        return loaded
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


DOTENV_LOADED = [] if os.environ.get("T2L_NO_DOTENV") == "1" else load_dotenv(BASE_DIR / ".env")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "static"


@dataclass(frozen=True)
class Settings:
    gemini_model: str          # current Flash model ID (run `python -m scripts.smoke_gemini` to list them)
    google_api_key: str        # AI Studio key ("AIza...") or Vertex AI express-mode key ("AQ....")
    gemini_auth: str           # auto | ai_studio | vertex_express | vertex_adc
    gcp_project: str
    gcp_location: str
    mock_llm: bool             # MOCK_LLM=1 -> canned Gemini outputs; trend data is still fetched live
    offline: bool              # OFFLINE=1 -> no network at all (tests/CI); synthetic, clearly labelled curves
    cache_dir: Path
    wiki_user_agent: str
    max_writer_attempts: int
    http_cache_hours: float
    run_store: str             # firestore | local | off   (auto: firestore on Cloud Run with a project)
    grounding: bool            # Gemini + Google Search to discover real launches (live mode)
    bigquery: bool             # BigQuery Google Trends radar
    bq_max_gb: float           # hard cap on bytes billed per BigQuery query

    @property
    def resolved_auth(self) -> str:
        """Express-mode keys start with 'AQ.' and must be sent to Vertex AI, not AI Studio."""
        if self.gemini_auth != "auto":
            return self.gemini_auth
        if self.google_api_key.startswith("AQ."):
            return "vertex_express"
        if self.google_api_key:
            return "ai_studio"
        return "vertex_adc"


def get_settings() -> Settings:
    env = os.environ.get
    auth = env("GEMINI_AUTH", "auto")
    if env("USE_VERTEX", "0") == "1" and auth == "auto":  # backwards compatible
        auth = "vertex_adc"
    return Settings(
        gemini_model=env("GEMINI_MODEL", ""),
        google_api_key=env("GOOGLE_API_KEY", ""),
        gemini_auth=auth,
        gcp_project=env("GOOGLE_CLOUD_PROJECT", ""),
        gcp_location=env("GOOGLE_CLOUD_LOCATION", "global"),
        # MOCK_LLM unset and no credentials at all -> mock, so a fresh clone runs instead of failing at Gemini
        mock_llm=(env("MOCK_LLM") == "1") if env("MOCK_LLM") is not None
        else not (env("GOOGLE_API_KEY") or env("GOOGLE_CLOUD_PROJECT")),
        offline=env("OFFLINE", "0") == "1",
        cache_dir=Path(env("CACHE_DIR", "/tmp/t2l_cache")),
        wiki_user_agent=env(
            "WIKI_USER_AGENT",
            "Trend2Line/0.2 (AI Builder Cup prototype; set WIKI_USER_AGENT with a contact email)",
        ),
        max_writer_attempts=int(env("MAX_WRITER_ATTEMPTS", "3")),
        http_cache_hours=float(env("HTTP_CACHE_HOURS", "12")),
        run_store=_run_store(env("RUN_STORE", "auto"), env("GOOGLE_CLOUD_PROJECT", ""), env("K_SERVICE", "")),
        grounding=env("ENABLE_GROUNDING", "1") == "1",
        bigquery=env("ENABLE_BIGQUERY", "1") == "1",
        bq_max_gb=float(env("BQ_MAX_GB", "20")),
    )


def _run_store(value: str, project: str, k_service: str) -> str:
    """auto -> Firestore when running on Cloud Run (K_SERVICE is set there) with a project; else local files."""
    if value != "auto":
        return value
    return "firestore" if (project and k_service) else "local"
