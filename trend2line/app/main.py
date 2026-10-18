"""FastAPI entrypoint for Cloud Run. Cloud Run sets $PORT; the Dockerfile passes it to uvicorn."""
import datetime as dt
import logging

import yaml
import hmac

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse

from . import pipeline
from .config import DATA_DIR, STATIC_DIR, get_settings
from .llm import LLM, LLMError
from .mock import MOCK_HANDLERS
from .stages import assist, catalog, radar, watch
from .stages.trend import WikimediaError
from .storage import get_store
import time as _time

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("trend2line")
app = FastAPI(title="Trend2Line", version="0.2.0")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PLANT_BYTES = 256 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


def _plant_from_upload(upload: UploadFile | None) -> dict | None:
    if upload is None or not upload.filename:
        return None
    raw = upload.file.read(MAX_PLANT_BYTES + 1)
    if len(raw) > MAX_PLANT_BYTES:
        raise HTTPException(400, "Plant file larger than 256 KB")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level must be a mapping")
        return catalog.prepare_plant({**data, "provenance": data.get("provenance", "uploaded_company_input")})
    except (ValueError, yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Plant file is not valid: {exc}")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


def _kind(plant: dict) -> str:
    """demo = synthetic demo company, public = real products from Open Food Facts, sample = illustrative plant."""
    p = plant.get("provenance", "")
    return "demo" if p == "demo_company_input" else "public" if p == "public_pack_data_inferred_lines" else "sample"


def _services(s) -> dict:
    live = not s.mock_llm and not s.offline
    return {
        "gemini": {"live": live, "auth": s.resolved_auth, "model": s.gemini_model or None,
                   "platform": "Vertex AI" if s.resolved_auth.startswith("vertex") else "Gemini API (AI Studio)"},
        "grounding": live and s.grounding,
        "bigquery": bool(s.bigquery and s.gcp_project and not s.offline),
        "run_store": s.run_store,
        "project": s.gcp_project or None,
    }


@app.get("/api/config")
def config() -> dict:
    s = get_settings()
    ref = catalog.fad_reference()
    plant = catalog.default_plant()
    mk = catalog.markets()["markets"]
    return {
        "mode": "offline" if s.offline else ("mock_llm" if s.mock_llm else "live"),
        "services": _services(s),
        "model": s.gemini_model or None,
        "auth": s.resolved_auth,
        "reference_status": ref.get("status"),
        "data_refreshed_at": catalog.manifest().get("refreshed_at"),
        "company": plant.get("company"),
        "home_market": plant.get("home_market"),
        "skus": {k: f"{v['name']} ({v['line']})" for k, v in plant["skus"].items()},
        "companies": {"": {"name": f"{plant.get('company')} (sample)", "home_market": plant.get("home_market"),
                           "kind": "sample", "lines": len(plant["lines"]),
                           "note": "Illustrative multi-category plant",
                           "skus": {k: f"{v['name']} ({v['line']})" for k, v in plant["skus"].items()}}} | {
            cid: {"name": c.get("company", cid) + (" (demo)" if _kind(c) == "demo" else ""),
                  "home_market": c.get("home_market"), "note": c.get("note", ""), "kind": _kind(c),
                  "lines": len(c["lines"]), "products": len(c["skus"]) if _kind(c) == "public" else 0,
                  "built_on": c.get("built_on"),
                  "skus": {k: f"{v['name']} ({v['line']})" for k, v in c["skus"].items()}}
            for cid, c in sorted(catalog.companies().items(), key=lambda kv: (_kind(kv[1]) != "demo", kv[0]))},
        "reference": {"status": ref.get("status"), "computed_on": ref.get("computed_on"), "n_fads": len(ref.get("fads", [])),
                      "half_life_days": {k: v["half_life_days"]["median"] for k, v in ref.get("classes", {}).items()}},
        "impact": catalog.impact_baseline(),
        "scenarios": [{k: (v if isinstance(v, list) else str(v)) for k, v in sc.items()} for sc in catalog.demo_scenarios()],
        "markets": {k: {"name": v["name"], "status": v["allergen_source"]["status"],
                        "label_options": {c: assist.LANGUAGE_NAMES[c] for c in assist.label_languages(v)}}
                    for k, v in mk.items()},
        "demo_trends": [{k: str(d[k]) for k in ("trend", "origin", "suggested_as_of", "suggested_market")}
                        for d in catalog.demo_trends()],
        "today": dt.date.today().isoformat(),
    }


@app.get("/api/provenance")
def provenance() -> dict:
    mk = catalog.markets()["markets"]
    return {
        "manifest": catalog.manifest(),
        "fad_reference": {k: v for k, v in catalog.fad_reference().items() if k != "fads"},
        "markets": {k: {"name": v["name"], "allergen_source": {a: str(b) for a, b in v["allergen_source"].items()},
                        "rules": [{"rule_id": r["rule_id"], **r["source"]} for r in v["rules"]]}
                    for k, v in mk.items()},
    }


@app.get("/api/plant/template")
def plant_template() -> FileResponse:
    return FileResponse(DATA_DIR / "plant.yaml", media_type="text/yaml", filename="plant.yaml")


@app.post("/api/plant/validate")
def plant_validate(plant_file: UploadFile = File(...)) -> dict:
    plant = _plant_from_upload(plant_file)
    return {"company": plant.get("company"), "skus": {k: f"{v['name']} ({v['line']})" for k, v in plant["skus"].items()},
            "lines": {k: {"name": v.get("name", k), "allergens_present": v["allergens_present"]}
                      for k, v in plant["lines"].items()}}


@app.post("/api/run")
def run(trend: str = Form(..., min_length=2, max_length=120), sku_id: str = Form(...),
        as_of: str = Form(...), market: str = Form(""), scan: bool = Form(True),
        image: UploadFile | None = File(None), plant_file: UploadFile | None = File(None),
        company: str = Form("")) -> dict:
    s = get_settings()
    try:
        as_of_date = dt.date.fromisoformat(as_of)
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    if as_of_date > dt.date.today():
        raise HTTPException(400, "as_of cannot be in the future")
    img = None
    if image is not None and image.filename:
        if image.content_type not in ALLOWED_MIME:
            raise HTTPException(400, f"Image type {image.content_type} not supported")
        data = image.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(400, "Image larger than 8 MB")
        img = (data, image.content_type)
    plant = _plant_from_upload(plant_file)
    if plant is None and company:
        plant = catalog.companies().get(company)
        if plant is None:
            raise HTTPException(400, f"Unknown company profile {company}")
    market = market or (plant or catalog.default_plant()).get("home_market") or next(iter(catalog.markets()["markets"]))
    llm = LLM(s, MOCK_HANDLERS)
    try:
        result = pipeline.run(trend.strip(), sku_id, as_of_date, market, img, llm, s, plant=plant, scan=scan)
        result["company"] = (plant or catalog.default_plant()).get("company", "")
        result["company_id"] = "" if plant_file is not None and plant_file.filename else company
        result["services"] = _services(s)
        store = get_store(s)
        if store:
            try:
                result["run_id"] = store.save(result)
                result["run_store"] = store.kind
            except Exception as exc:  # storing must never lose the answer
                log.warning("run store failed: %s", exc)
                result["run_store_error"] = str(exc)[:200]
        return result
    except pipeline.PipelineError as exc:
        raise HTTPException(400, str(exc))
    except LLMError as exc:
        log.exception("LLM failure")
        raise HTTPException(503, f"Gemini call failed: {exc}")
    except WikimediaError as exc:
        log.warning("Wikimedia failure: %s", exc)
        raise HTTPException(503, f"Wikipedia data unavailable: {exc}")
    except Exception as exc:  # never answer a bare 500: tell the user what broke
        log.exception("run failed")
        raise HTTPException(500, f"Analysis failed ({type(exc).__name__}): {str(exc)[:300]}")


@app.get("/api/runs")
def runs(limit: int = 20) -> dict:
    s = get_settings()
    store = get_store(s)
    if not store:
        return {"store": "off", "runs": []}
    try:
        return {"store": store.kind, "runs": store.list(min(limit, 100))}
    except Exception as exc:
        raise HTTPException(503, f"Run history unavailable ({store.kind}): {exc}")


@app.get("/api/runs/{run_id}")
def run_get(run_id: str) -> dict:
    store = get_store(get_settings())
    result = store.get(run_id) if store else None
    if result is None:
        raise HTTPException(404, "Run not found")
    return result


_RADAR_CACHE: dict[int, tuple[float, dict]] = {}


@app.get("/api/radar")
def radar_scan(days: int = 14) -> dict:
    """Rising Google searches per market (BigQuery public Google Trends), filtered to food trends by Gemini."""
    s = get_settings()
    if not _services(s)["bigquery"]:
        raise HTTPException(400, "BigQuery radar needs GOOGLE_CLOUD_PROJECT and ENABLE_BIGQUERY=1 (and no OFFLINE).")
    days = max(1, min(days, 60))
    hit = _RADAR_CACHE.get(days)
    if hit and _time.time() - hit[0] < 6 * 3600:
        return hit[1]
    markets = catalog.markets()["markets"]
    codes = sorted({c for m in markets.values() for c in m.get("countries", [])})
    try:
        rows = radar.query(s.gcp_project, codes, days, s.bq_max_gb)
        out = radar.build(rows, markets, LLM(s, MOCK_HANDLERS))
    except LLMError as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        log.exception("BigQuery radar failed")
        raise HTTPException(503, f"BigQuery query failed: {exc}")
    out["days"] = days
    _RADAR_CACHE[days] = (_time.time(), out)
    return out


# --- Assistants on a finished run (Gemini writes, code checks) ----------------------------------------------
class RunRef(BaseModel):
    run_id: str = ""
    run: dict | None = None   # the run itself, when run history is off


def _load_run(ref: RunRef) -> dict:
    if ref.run_id:
        store = get_store(get_settings())
        found = store.get(ref.run_id) if store else None
        if found:
            return found
    if ref.run and all(k in ref.run for k in ("decision", "compliance", "evidence", "spec", "lines", "bom")):
        return ref.run
    raise HTTPException(400, "Run not found: pass a saved run_id or the run result.")


class LabelReq(RunRef):
    language: str = ""


@app.post("/api/label")
def label(req: LabelReq = Body(...)) -> dict:
    """Gemini drafts the ingredient and allergen statements in the market's language; code checks them."""
    run = _load_run(req)
    options = assist.label_languages(catalog.markets()["markets"][run["compliance"]["market"]])
    language = req.language or next((o for o in options if o != "en"), "en")  # the local language first
    if language not in options:
        raise HTTPException(400, f"Language {language} is not offered for this market ({', '.join(options)}).")
    try:
        return assist.draft_label(run, language, LLM(get_settings(), MOCK_HANDLERS)) | {"options": options}
    except LLMError as exc:
        raise HTTPException(503, f"Gemini call failed: {exc}")


class AskReq(RunRef):
    question: str = Field(..., min_length=3, max_length=300)


@app.post("/api/ask")
def ask(req: AskReq = Body(...)) -> dict:
    """Answer a question about the run from its evidence only; every claim is verified like the write-up."""
    run = _load_run(req)
    try:
        return assist.ask(run, req.question.strip(), LLM(get_settings(), MOCK_HANDLERS))
    except LLMError as exc:
        raise HTTPException(503, f"Gemini call failed: {exc}")


class CompareReq(BaseModel):
    trend: str = Field(..., min_length=2, max_length=120)
    as_of: str
    by: str = Field("companies", pattern="^(companies|markets)$")
    market: str = ""
    company: str = ""
    markets: list[str] = Field(default_factory=list, max_length=6)


def _row(result: dict, company_id: str, plant: dict, market: str) -> dict:
    d = result["decision"]
    blast = result["allergen_blast_radius"]
    return {"company_id": company_id, "company": plant.get("company", ""), "kind": _kind(plant),
            "market": market, "market_name": catalog.markets()["markets"][market]["name"],
            "verdict": d["verdict"], "route": d["route"], "line": d["line"], "days_to_shelf": d["days_to_shelf"],
            "capture": d["capture_median"], "deadline_go_days": d["deadline_go_days"], "slack_days": d["slack_days"],
            "hard_failures": d["hard_failures"], "caveats": d["downgrade_reasons"],
            "claims_at_risk": len({(x["sku_id"], x["claim"]) for x in blast}),
            "products_at_risk": len({x["sku_id"] for x in blast}), "stage": result["trend"]["stage"],
            "basis": result["trend"]["basis"], "declarations": result["compliance"]["allergens_to_declare"],
            "label_languages_needed": result["compliance"]["label_languages_needed"]}


@app.post("/api/compare")
def compare(req: CompareReq = Body(...)) -> dict:
    """The same trend for every company (one market), or for one company across markets. Quick runs: same
    verdict rules, no live launch discovery and no write-up."""
    s = get_settings()
    try:
        as_of_date = dt.date.fromisoformat(req.as_of)
    except ValueError:
        raise HTTPException(400, "as_of must be YYYY-MM-DD")
    markets = catalog.markets()["markets"]
    if req.by == "companies":
        plants = [("", catalog.default_plant())] + list(catalog.companies().items())
        jobs = [(cid, p, req.market or p.get("home_market")) for cid, p in plants]
    else:
        plant = catalog.companies().get(req.company) if req.company else catalog.default_plant()
        if plant is None:
            raise HTTPException(400, f"Unknown company profile {req.company}")
        jobs = [(req.company, plant, m) for m in (req.markets or [plant.get("home_market")])]
    rows, llm = [], LLM(s, MOCK_HANDLERS)
    for cid, plant, market in jobs:
        if market not in markets:
            raise HTTPException(400, f"Unknown market {market}")
        try:
            result = pipeline.run(req.trend.strip(), next(iter(plant["skus"])), as_of_date, market, None, llm, s,
                                  plant=plant, scan=False, quick=True)
            rows.append(_row(result, cid, plant, market))
        except (pipeline.PipelineError, WikimediaError, LLMError) as exc:
            rows.append({"company_id": cid, "company": plant.get("company", ""), "kind": _kind(plant),
                         "market": market, "market_name": markets[market]["name"], "error": str(exc)[:300]})
    return {"trend": req.trend, "as_of": req.as_of, "by": req.by, "rows": rows}


# --- Opportunity watch (Cloud Scheduler calls /api/watch/run once a day) ------------------------------------
@app.get("/api/watch")
def watch_latest() -> dict:
    store = get_store(get_settings())
    if not store:
        return {"status": "off"}
    try:
        latest = store.get_watch()
    except Exception as exc:
        raise HTTPException(503, f"Watch results unavailable ({store.kind}): {exc}")
    return latest | {"status": "ok"} if latest else {"status": "never_run"}


@app.post("/api/watch/run")
def watch_run(x_watch_token: str = Header("")) -> dict:
    s = get_settings()
    if s.watch_token:
        if not hmac.compare_digest(x_watch_token, s.watch_token):
            raise HTTPException(403, "Wrong or missing X-Watch-Token.")
    elif s.on_cloud_run:
        raise HTTPException(403, "Set WATCH_TOKEN (deploy.sh does) to enable the scheduled watch.")
    try:
        radar_out = radar_scan(14) if _services(s)["bigquery"] else None
    except HTTPException as exc:
        log.warning("watch: radar unavailable (%s); using the demo trend list", exc.detail)
        radar_out = None
    cands = watch.candidates(radar_out)
    result = watch.run(cands, dt.date.today() - dt.timedelta(days=1), LLM(s, MOCK_HANDLERS), s)
    store = get_store(s)
    if store:
        try:
            store.save_watch(result)
        except Exception as exc:
            result["store_error"] = str(exc)[:200]
    return result
