"""Tools the ADK agent calls. Plain functions over the same pipeline as the web app (no ADK import here, so they
are unit-tested with the rest of the app). They return the decision computed by code; the agent only explains it.
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import pipeline  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.llm import LLM  # noqa: E402
from app.mock import MOCK_HANDLERS  # noqa: E402
from app.stages import catalog  # noqa: E402


def _plant(company: str) -> dict:
    plant = catalog.companies().get(company) if company else catalog.default_plant()
    if plant is None:
        raise ValueError(f"Unknown company '{company}'. Call list_options for the valid ids.")
    return plant


def _day(as_of: str) -> dt.date:
    return dt.date.fromisoformat(as_of) if as_of else dt.date.today() - dt.timedelta(days=1)


def list_options() -> dict:
    """List the company profiles and markets this agent can assess.

    Returns:
        companies: {id: name, home market, kind}; markets: {id: name}.
    """
    cos = {"": catalog.default_plant()} | catalog.companies()
    return {"companies": {cid: {"name": p.get("company"), "home_market": p.get("home_market"),
                                "kind": p.get("provenance")} for cid, p in cos.items()},
            "markets": {k: v["name"] for k, v in catalog.markets()["markets"].items()}}


def assess_trend(trend: str, company: str = "", market: str = "", as_of: str = "") -> dict:
    """Decide whether a company should launch a product for a viral food trend in a market.

    Args:
        trend: Trend name, e.g. "Tanghulu" or "Dubai chocolate".
        company: Company id from list_options (empty for the sample plant), e.g. "hanbit_foods".
        market: Market id, e.g. "KR", "IN", "GCC". Defaults to the company's home market.
        as_of: Run date YYYY-MM-DD; defaults to yesterday (the latest complete Wikipedia day).
    """
    s = get_settings()
    plant = _plant(company)
    market = market or plant.get("home_market") or "IN"
    r = pipeline.run(trend, next(iter(plant["skus"])), _day(as_of), market, None, LLM(s, MOCK_HANDLERS), s,
                     plant=plant, scan=False, quick=True)
    d = r["decision"]
    return {"trend": r["trend"].get("article"), "company": plant.get("company"), "market": market,
            "as_of": r["as_of"], "verdict": d["verdict"], "interest_left_at_launch": d["capture_median"],
            "days_to_shelf": d["days_to_shelf"], "shelf_deadline_days": d["deadline_go_days"],
            "shelf_deadline_date": d["deadline_go_date"], "route": d["route"], "line": d["line"],
            "hard_failures": d["hard_failures"], "caveats": d["downgrade_reasons"],
            "allergen_blast_radius": sorted({f"{x['sku_name']}: {x['claim_label']}" for x in r["allergen_blast_radius"]}),
            "must_declare": r["compliance"]["allergens_to_declare"],
            "label_languages_to_add": r["compliance"]["label_languages_needed"]}


def compare_companies(trend: str, market: str, as_of: str = "") -> list[dict]:
    """Run the same trend, market and date for every company profile and return each verdict.

    Args:
        trend: Trend name.
        market: Market id, e.g. "KR".
        as_of: Run date YYYY-MM-DD; defaults to yesterday.
    """
    rows = []
    for cid in list_options()["companies"]:
        try:
            a = assess_trend(trend, cid, market, as_of)
            rows.append({k: a[k] for k in ("company", "verdict", "interest_left_at_launch", "days_to_shelf",
                                           "route", "hard_failures")})
        except (pipeline.PipelineError, ValueError) as exc:
            rows.append({"company": cid or "sample", "error": str(exc)})
    return rows
