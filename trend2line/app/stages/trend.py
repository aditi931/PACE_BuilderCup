"""Trend Scout: Wikipedia pageviews per language edition as a public, regional demand proxy.

One article is followed across languages through Wikidata sitelinks (en 'Dubai chocolate' -> ja, ar, de ...),
so each market is read from its own-language edition. All maths is deterministic.
Network calls are cached on disk (HTTP_CACHE_HOURS) and never made when OFFLINE=1.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import time
from pathlib import Path
from urllib.parse import quote

import httpx

WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{lang}.wikipedia/"
             "all-access/user/{title}/daily/{start}/{end}")
Series = list[tuple[dt.date, int]]


class Http:
    """Tiny GET-JSON helper with a disk cache, so repeated demo runs are fast and polite to Wikimedia."""

    def __init__(self, user_agent: str, cache_dir: Path | None = None, ttl_hours: float = 12):
        self.ua, self.ttl = user_agent, ttl_hours * 3600
        self.dir = (cache_dir / "http") if cache_dir else None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
        self.requests = 0

    def get(self, url: str, params: dict | None = None, allow_404: bool = False):
        key = hashlib.sha256((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[:32]
        path = self.dir / f"{key}.json" if self.dir else None
        if path and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return json.loads(path.read_text(encoding="utf-8"))
        r = httpx.get(url, params=params, headers={"User-Agent": self.ua}, timeout=20)
        self.requests += 1
        if r.status_code == 404 and allow_404:
            data = None
        else:
            r.raise_for_status()
            data = r.json()
        if path:
            path.write_text(json.dumps(data), encoding="utf-8")
        return data


def search_candidates(http: Http, query: str, lang: str = "en", limit: int = 5) -> list[str]:
    data = http.get(WIKI_API.format(lang=lang), {"action": "opensearch", "search": query, "limit": limit,
                                                  "namespace": 0, "format": "json"})
    return list(data[1]) if data else []


def wikidata_qid(http: Http, title: str, lang: str = "en") -> str | None:
    data = http.get(WIKI_API.format(lang=lang), {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                                  "redirects": 1, "titles": title, "format": "json"})
    for page in (data or {}).get("query", {}).get("pages", {}).values():
        if qid := page.get("pageprops", {}).get("wikibase_item"):
            return qid
    return None


def sitelinks(http: Http, qid: str) -> dict[str, str]:
    """{language code: article title} for every Wikipedia edition that has the article."""
    data = http.get(WIKIDATA_API, {"action": "wbgetentities", "ids": qid, "props": "sitelinks", "format": "json"})
    links = (data or {}).get("entities", {}).get(qid, {}).get("sitelinks", {})
    out = {}
    for site, v in links.items():
        if site.endswith("wiki") and site not in ("commonswiki", "specieswiki", "metawiki", "wikidatawiki"):
            out[site[:-4].replace("_", "-")] = v["title"]
    return out


def fetch_pageviews(http: Http, title: str, start: dt.date, end: dt.date, lang: str = "en") -> Series:
    url = PAGEVIEWS.format(lang=lang, title=quote(title.replace(" ", "_"), safe=""),
                           start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
    data = http.get(url, allow_404=True)
    if not data:
        return []
    return [(dt.datetime.strptime(i["timestamp"][:8], "%Y%m%d").date(), int(i["views"])) for i in data.get("items", [])]


def combine(series_list: list[Series]) -> Series:
    """Sum several daily series by date (missing days count as zero)."""
    acc: dict[dt.date, int] = {}
    for s in series_list:
        for d, v in s:
            acc[d] = acc.get(d, 0) + v
    return sorted(acc.items())


def synthetic_series(start: dt.date, end: dt.date, peak: dt.date, rise_days: int,
                     half_life: int, peak_views: int = 20000, baseline: int = 40) -> Series:
    """Clearly synthetic curve for OFFLINE mode only. Never presented as real data."""
    out, d = [], start
    while d <= end:
        delta = (d - peak).days
        if delta <= 0:
            v = baseline + peak_views * math.exp(-((delta / max(rise_days / 2.5, 1)) ** 2))
        else:
            v = baseline + peak_views * 0.5 ** (delta / half_life)
        out.append((d, int(v)))
        d += dt.timedelta(days=1)
    return out


def offline_series(trend: str, lang: str, as_of: dt.date, peak: dt.date | None = None) -> Series:
    """Deterministic synthetic curve per (trend, language) so offline tests are stable."""
    h = int(hashlib.sha256(f"{trend.lower()}|{lang}".encode()).hexdigest(), 16)
    peak = peak or (as_of - dt.timedelta(days=10 + h % 50))
    scale = 0.2 + (h % 80) / 100 if lang != "en" else 1.0
    return synthetic_series(as_of - dt.timedelta(days=3 * 365), as_of, peak, rise_days=10 + h % 15,
                            half_life=20 + h % 90, peak_views=int(20000 * scale))


def rolling_mean(values: list[float], window: int) -> list[float]:
    out, total = [], 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        out.append(total / min(i + 1, window))
    return out


def capture_share(days_to_shelf: float, half_life_days: float) -> float:
    """Share of today's interest still left when the product lands: 0.5 ** (T / h)."""
    return round(0.5 ** (days_to_shelf / half_life_days), 3)


def days_until_share(share: float, half_life_days: float) -> int:
    """Inverse of capture_share: the last whole day on which at least `share` of today's interest is left.
    Solves 0.5 ** (T / h) = share  ->  T = h * log2(1 / share). Rounded down, so the deadline is never late."""
    if not 0 < share < 1:
        raise ValueError("share must be between 0 and 1")
    return int(math.floor(half_life_days * math.log2(1 / share)))


def shelf_deadline(half_life: dict, as_of: dt.date, go_share: float, conditional_share: float,
                   sustained: bool) -> dict:
    """'Must be on shelf within N days', from public data only (no company data needed).

    For each half-life scenario (p25 = fast-fading fad, median, p75 = slow fade), the number of days from the
    run date until interest drops below the GO share and the CONDITIONAL share. A sustained trend has no deadline.
    """
    out = {"sustained": sustained, "go_share": go_share, "conditional_share": conditional_share,
           "method": "T = half_life * log2(1 / share); half-lives from data/fad_reference.json"}
    for name, share in (("go", go_share), ("conditional", conditional_share)):
        if sustained:
            out[f"{name}_by_days"] = None
            out[f"{name}_by_date"] = None
            continue
        days = {k: days_until_share(share, v) for k, v in half_life.items()}
        out[f"{name}_by_days"] = days
        out[f"{name}_by_date"] = {k: (as_of + dt.timedelta(days=d)).isoformat() for k, d in days.items()}
    return out


def analyze(series: Series, as_of: dt.date, reference: dict, smooth_window: int = 7, min_points: int = 120) -> dict:
    pts = [(d, v) for d, v in series if d <= as_of]
    if len(pts) < min_points or sum(v for _, v in pts) == 0:
        return {"stage": "insufficient_data", "points": len(pts)}
    dates = [d for d, _ in pts]
    sm = rolling_mean([float(v) for _, v in pts], smooth_window)
    cur = sm[-1]
    peak_i = max(range(len(sm)), key=sm.__getitem__)
    peak, peak_date = sm[peak_i], dates[peak_i]
    prev90 = sm[-91] if len(sm) > 90 else sm[0]
    momentum = min(cur / prev90, 99.0) if prev90 > 0 else 99.0
    days_since_peak = (as_of - peak_date).days

    i = peak_i
    while i > 0 and sm[i] > 0.1 * peak:
        i -= 1
    rise_days = (peak_date - dates[i]).days

    observed_half_life = None
    for j in range(peak_i + 1, len(sm)):
        if sm[j] < 0.5 * peak:
            observed_half_life = (dates[j] - peak_date).days
            break

    if days_since_peak >= 365 and cur >= 0.4 * peak:
        stage = "sustained"
    elif cur < 0.8 * peak and days_since_peak > 14:
        stage = "decaying"
    elif momentum > 1.5:
        stage = "rising"
    else:
        stage = "peaking"

    cls = "flash" if rise_days <= reference["flash_rise_days_max"] else "standard"
    hl = reference["classes"][cls]["half_life_days"]
    weekly = [[dates[k].isoformat(), round(sm[k], 1)] for k in range(0, len(sm), 7)]
    return {
        "stage": stage,
        "momentum_90d": round(momentum, 2),
        "current_views_smoothed": round(cur, 1),
        "peak_views_smoothed": round(peak, 1),
        "peak_date": peak_date.isoformat(),
        "days_since_peak": days_since_peak,
        "rise_days": rise_days,
        "observed_half_life_days": observed_half_life,
        "reference_class": cls,
        "reference_half_life_days": {k: v for k, v in hl.items()},
        "reference_status": reference.get("status", "unknown"),
        "series_weekly": weekly,
    }
