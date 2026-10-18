"""Check Gemini credentials and list usable models.

    set -a && source .env && set +a
    python -m scripts.smoke_gemini

Shows which auth path is used (AI Studio key, Vertex express-mode key "AQ....", or Vertex ADC), lists the
models your key can see, and makes one tiny structured call with GEMINI_MODEL if it is set.
"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):  # Windows consoles: print non-English titles safely
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pydantic import BaseModel

from app.config import get_settings
from app.llm import LLM


class Ping(BaseModel):
    ok: bool
    model_family: str


def main() -> None:
    s = get_settings()
    print(f"auth path: {s.resolved_auth}   key set: {bool(s.google_api_key)}   GEMINI_MODEL: {s.gemini_model or '(unset)'}")
    llm = LLM(s)
    try:
        names = llm.list_models()
        print("Gemini models visible to these credentials:")
        for n in names:
            print("  ", n)
    except Exception as exc:
        print(f"could not list models ({exc}); for Vertex AI run `gcloud auth application-default login` "
              "and set GOOGLE_CLOUD_PROJECT")
    if s.gemini_model:
        r = llm.structured("smoke", "Reply with ok=true and your model family name.", Ping)
        print("structured call OK:", r)


if __name__ == "__main__":
    main()
