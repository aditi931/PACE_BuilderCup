"""Opportunity watch: once a day (Cloud Scheduler -> POST /api/watch/run), run the rising food trends against every
company profile and keep the result, so the team opens the app to a ranked list of windows instead of a blank form.

Candidates come from the BigQuery trend radar (rising Google searches per market, food terms picked by Gemini).
Without BigQuery they come from the demo trend list, labelled as such. Each run is a quick pipeline run: the same
verdict rules, without live launch discovery or the write-up. A time budget keeps the job inside Cloud Run's
request timeout; whatever does not fit is counted as skipped.
"""
from __future__ import annotations

import datetime as dt
import time

from .. import pipeline
from . import catalog

ORDER = {"GO": 0, "CONDITIONAL": 1, "NO-GO": 2}


def candidates(radar_out: dict | None, limit: int = 8) -> list[dict]:
    if radar_out and radar_out.get("food_trends"):
        seen, out = set(), []
        for t in radar_out["food_trends"]:
            key = (t["english_name"].strip().lower(), t.get("market"))
            if not t.get("market") or key in seen:
                continue
            seen.add(key)
            out.append({"trend": t["english_name"].strip(), "market": t["market"], "source": "bigquery_radar",
                        "detail": f"+{t.get('percent_gain')}% Google searches in {t.get('country')}, {t.get('last_seen')}"})
            if len(out) >= limit:
                break
        return out
    return [{"trend": d["trend"], "market": d["suggested_market"], "source": "demo_list",
             "detail": "BigQuery radar off: demo trend list"} for d in catalog.demo_trends()][:limit]


def run(cands: list[dict], as_of: dt.date, llm, settings, budget_s: float = 230, max_runs: int = 30) -> dict:
    companies = [("", catalog.default_plant())] + list(catalog.companies().items())
    rows, skipped, t0 = [], 0, time.time()
    for c in cands:
        for cid, plant in companies:
            if len(rows) >= max_runs or time.time() - t0 > budget_s:
                skipped += 1
                continue
            base = {"trend": c["trend"], "market": c["market"], "company_id": cid,
                    "company": plant.get("company", ""), "signal": c["detail"]}
            try:
                r = pipeline.run(c["trend"], next(iter(plant["skus"])), as_of, c["market"], None, llm, settings,
                                 plant=plant, scan=False, quick=True)
            except pipeline.PipelineError as exc:  # the trend itself has no usable data: skip its other companies
                rows.append(base | {"error": str(exc)[:200]})
                break
            except Exception as exc:
                rows.append(base | {"error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                continue
            d = r["decision"]
            rows.append(base | {"article": r["trend"].get("article"), "sku_id": next(iter(plant["skus"])),
                                "verdict": d["verdict"], "capture": d["capture_median"],
                                "days_to_shelf": d["days_to_shelf"], "deadline_go_days": d["deadline_go_days"],
                                "slack_days": d["slack_days"], "route": d["route"], "line": d["line"],
                                "stage": r["trend"]["stage"], "hard_failures": d["hard_failures"],
                                "claims_at_risk": len({(x["sku_id"], x["claim"]) for x in r["allergen_blast_radius"]})})
    rows.sort(key=lambda x: (ORDER.get(x.get("verdict"), 3), -(x.get("capture") or 0)))
    return {"ran_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "as_of": as_of.isoformat(),
            "source": cands[0]["source"] if cands else "none", "trends": len(cands),
            "runs": sum(1 for x in rows if "verdict" in x), "skipped": skipped, "rows": rows}
