"""Real reference launches and the backtest. Pure code over public data.

For a trend with known real launches (data/reference_launches.yaml):
  * each market's OBSERVED trend window from its own-language Wikipedia curve: take-off (10% of peak), peak,
    and the day interest fell back below half the peak (the window closing);
  * every real launch placed in that window: days after take-off, days after peak, share of peak left;
  * an OBSERVED time-to-market benchmark: how long real companies took from take-off to shelf. The pipeline
    uses it instead of sample plant durations, so the verdict rests on real numbers;
  * a MODEL CHECK: the half-life the fad reference predicts vs the half-life actually observed.
"""
from __future__ import annotations

import datetime as dt
import statistics

from . import trend as T
from .ingredients import norm

TAKEOFF_SHARE = 0.10


def match(trend: str, cfg: dict) -> tuple[str, dict] | tuple[None, None]:
    t = norm(trend)
    for key, entry in (cfg.get("trends") or {}).items():
        for alias in entry.get("match", []):
            a = norm(alias)
            if t == a or a in t or t in a:
                return key, entry
    return None, None


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def _curve(store, langs: list[str]) -> tuple[list[dt.date], list[float], list[str]]:
    native = [lg for lg in langs if lg != "en" and store.get(lg)]
    used = native or (["en"] if store.get("en") else [])
    series = T.combine([store.get(lg) for lg in used])
    return [d for d, _ in series], T.rolling_mean([float(v) for _, v in series], 7), used


def window(dates: list[dt.date], sm: list[float], as_of: dt.date) -> dict | None:
    """Observed trend window in one market: take-off, peak, and when interest fell below half of the peak."""
    upto = [i for i, d in enumerate(dates) if d <= as_of]
    if not upto or max(sm[i] for i in upto) <= 0:
        return None
    pk = max(upto, key=sm.__getitem__)
    i = pk
    while i > 0 and sm[i - 1] >= TAKEOFF_SHARE * sm[pk]:
        i -= 1
    half = next((j for j in range(pk + 1, upto[-1] + 1) if sm[j] < 0.5 * sm[pk]), None)
    return {"takeoff_date": dates[i].isoformat(), "peak_date": dates[pk].isoformat(),
            "window_closed_date": dates[half].isoformat() if half is not None else None,
            "rise_days": (dates[pk] - dates[i]).days,
            "observed_half_life_days": (dates[half] - dates[pk]).days if half is not None else None,
            "_idx": {"takeoff": i, "peak": pk}}


def evaluate(entry: dict, store, langs_by_market: dict[str, list[str]], as_of: dt.date) -> tuple[list[dict], dict]:
    rows, windows, curves = [], {}, {}
    markets = {ln["market"] for ln in entry.get("launches", [])} | ({entry["origin"]["market"]} if entry.get("origin") else set())
    for m in markets:
        dates, sm, used = _curve(store, langs_by_market.get(m, ["en"]))
        curves[m] = (dates, sm)
        w = window(dates, sm, as_of) if dates else None
        if w:
            windows[m] = {**{k: v for k, v in w.items() if k != "_idx"}, "languages_used": used}
    for ln in entry.get("launches", []):
        launch = _date(ln["date"])
        row = {k: ln.get(k) for k in ("company", "brand", "product", "market", "date_basis", "positioning",
                                       "source_title", "source_url", "made_in")}
        row["verification"] = ln.get("verification", "curated")
        row["date"] = launch.isoformat()
        w = windows.get(ln["market"])
        dates, sm = curves.get(ln["market"], ([], []))
        if launch > as_of:
            rows.append(row | {"status": "after_run_date"})
            continue
        if not w or launch < dates[0]:
            rows.append(row | {"status": "no_data"})
            continue
        li = max(i for i, d in enumerate(dates) if d <= launch)
        pk = dates.index(dt.date.fromisoformat(w["peak_date"]))
        rows.append(row | {
            "status": "ok",
            "languages_used": w["languages_used"],
            "days_after_takeoff": (launch - dt.date.fromisoformat(w["takeoff_date"])).days,
            "days_after_peak": (launch - dt.date.fromisoformat(w["peak_date"])).days,
            "interest_at_launch_share_of_peak": round(sm[li] / sm[pk], 3),
            "position": _position(launch, w),
        })
    return sorted(rows, key=lambda r: r["date"]), windows


def _position(launch: dt.date, w: dict) -> str:
    take, peak = dt.date.fromisoformat(w["takeoff_date"]), dt.date.fromisoformat(w["peak_date"])
    closed = dt.date.fromisoformat(w["window_closed_date"]) if w["window_closed_date"] else None
    if launch < take:
        return "before_takeoff"
    if launch <= peak:
        return "while_rising"
    if closed is None or launch <= closed:
        return "after_peak_window_open"
    return "after_window_closed"


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def benchmark(rows: list[dict], min_launches: int = 2) -> dict | None:
    """Observed time to market: days from take-off (in the launch market) to the real launch."""
    lags = [(r["days_after_takeoff"], r["brand"]) for r in rows if r.get("status") == "ok" and r["days_after_takeoff"] >= 0]
    if len(lags) < min_launches:
        return None
    vals = [v for v, _ in lags]
    return {"n": len(vals), "p25": _pct(vals, .25), "median": round(statistics.median(vals), 1), "p75": _pct(vals, .75),
            "fastest": min(lags)[1], "fastest_days": min(vals), "companies": sorted({b for _, b in lags}),
            "definition": f"days from take-off ({int(TAKEOFF_SHARE * 100)}% of peak interest in the launch market) "
                          "to the public launch date"}


def model_check(windows: dict, reference: dict) -> list[dict]:
    """Does the fad reference predict the half-life we actually observed in each market?"""
    out = []
    for m, w in windows.items():
        if w.get("observed_half_life_days") is None:
            continue
        cls = "flash" if w["rise_days"] <= reference["flash_rise_days_max"] else "standard"
        hl = reference["classes"][cls]["half_life_days"]
        obs = w["observed_half_life_days"]
        out.append({"market": m, "reference_class": cls, "predicted_median": hl["median"],
                    "predicted_range": [hl["p25"], hl["p75"]], "observed": obs,
                    "within_range": hl["p25"] <= obs <= hl["p75"], "reference_status": reference.get("status")})
    return out
