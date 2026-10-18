"""Compute the half-life reference table from real Wikimedia pageviews.

Usage: python -m scripts.build_fad_reference   (from the repo root, needs internet)
Candidates live in data/fad_candidates.csv (title, Wikipedia language, origin region), so the reference is
built from fads that started in different regions and languages. Titles that 404, are too short, or never
fall below 50% of their peak are skipped and reported. The Dockerfile runs this during the Cloud Run build.
"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):  # Windows consoles: print non-English titles safely
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import csv
import datetime as dt
import json
import os
import statistics
import sys

from app.config import DATA_DIR, get_settings
from app.stages.trend import Http, fetch_pageviews, rolling_mean

FLASH_RISE_DAYS_MAX = 21
START = dt.date(2015, 7, 1)


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 1)


def measure(series):
    dates = [d for d, _ in series]
    sm = rolling_mean([float(v) for _, v in series], 7)
    pk = max(range(len(sm)), key=sm.__getitem__)
    i = pk
    while i > 0 and sm[i] > 0.1 * sm[pk]:
        i -= 1
    rise = (dates[pk] - dates[i]).days
    half = next(((dates[j] - dates[pk]).days for j in range(pk + 1, len(sm)) if sm[j] < 0.5 * sm[pk]), None)
    return dates[pk], rise, half


def main() -> None:
    s = get_settings()
    http = Http(os.environ.get("WIKI_USER_AGENT", s.wiki_user_agent), s.cache_dir, s.http_cache_hours)
    end = dt.date.today() - dt.timedelta(days=2)
    with open(DATA_DIR / "fad_candidates.csv", newline="", encoding="utf-8") as f:
        candidates = list(csv.DictReader(f))
    rows, skipped = [], []
    for c in candidates:
        try:
            series = fetch_pageviews(http, c["title"], START, end, c["wiki"])
        except Exception as exc:
            skipped.append({**c, "reason": f"fetch failed: {exc}"})
            continue
        if len(series) < 180:
            skipped.append({**c, "reason": "missing or too short"})
            continue
        peak, rise, half = measure(series)
        if half is None:
            skipped.append({**c, "reason": "never fell below 50% of peak (sustained or still live)"})
            continue
        rows.append({**c, "peak_date": peak.isoformat(), "rise_days": rise, "half_life_days": half,
                     "class": "flash" if rise <= FLASH_RISE_DAYS_MAX else "standard"})
        print(f"  {c['wiki']}:{c['title']:<28} peak {peak} rise {rise:>4}d half-life {half:>4}d")

    placeholder = json.loads((DATA_DIR / "fad_reference.json").read_text(encoding="utf-8"))
    classes, complete = {}, True
    for cls in ("flash", "standard"):
        hl = [r["half_life_days"] for r in rows if r["class"] == cls]
        if len(hl) < 3:
            complete = False
            classes[cls] = {**placeholder["classes"][cls], "n": len(hl), "note": "too few fads; placeholder kept"}
            continue
        classes[cls] = {"half_life_days": {"p25": pct(hl, .25), "median": round(statistics.median(hl), 1),
                                           "p75": pct(hl, .75)}, "n": len(hl)}
    by_region: dict[str, list[int]] = {}
    for r in rows:
        by_region.setdefault(r["origin_region"], []).append(r["half_life_days"])
    out = {"status": "computed" if complete else "partial", "computed_on": dt.date.today().isoformat(),
           "method": "7-day smoothed Wikimedia pageviews per language edition; half-life = days from peak to "
                     "first day below 50% of peak; flash = rise from 10% to peak within "
                     f"{FLASH_RISE_DAYS_MAX} days",
           "flash_rise_days_max": FLASH_RISE_DAYS_MAX, "classes": classes,
           "median_half_life_by_origin_region": {k: statistics.median(v) for k, v in by_region.items()},
           "fads": rows, "skipped": skipped}
    if not complete and placeholder.get("status") == "computed":
        # e.g. Cloud Build could not reach Wikimedia: never replace a good reference with a worse one
        print(f"Only {len(rows)} fads measured; keeping the existing computed reference from "
              f"{placeholder.get('computed_on')}.")
        sys.exit(2)
    (DATA_DIR / "fad_reference.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(classes, indent=2), f"\nstatus {out['status']}; used {len(rows)}, skipped {len(skipped)}")
    if not complete:
        sys.exit(2)


if __name__ == "__main__":
    main()
