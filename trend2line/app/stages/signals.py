"""Per-market interest signals and the market scan. Deterministic; inputs come from data/ and Wikimedia.

A market is read from its own-language Wikipedia editions (ja for Japan, ar for the Gulf, pt for Brazil ...).
English is shared by many markets and cannot be split by country, so English-only markets are labelled
`shared_english` and the UI says so.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import trend as T


def market_languages(market: dict, indicators: dict[str, dict], aliases: dict[str, str], limit: int = 3) -> list[str]:
    if market.get("wiki_languages"):
        return list(market["wiki_languages"])
    langs: list[str] = []
    for iso2 in market.get("countries", []):
        for code in (indicators.get(iso2, {}).get("languages") or "").split(","):
            base = code.strip().split("-")[0].lower()
            base = aliases.get(base, base)
            if base and base not in langs:
                langs.append(base)
    return langs[:limit] or ["en"]


def aggregate_indicators(market: dict, indicators: dict[str, dict]) -> dict:
    pop = gdp = 0.0
    years, missing = set(), []
    for iso2 in market.get("countries", []):
        r = indicators.get(iso2)
        if not r or not r["population"] or not r["gdp_per_capita_usd"]:
            missing.append(iso2)
            continue
        pop += r["population"]
        gdp += r["population"] * r["gdp_per_capita_usd"]
        years.add(r["gdp_per_capita_year"])
    return {"population": int(pop), "gdp_usd": round(gdp), "gdp_per_capita_usd": round(gdp / pop) if pop else None,
            "indicator_years": sorted(years), "countries_missing_data": missing,
            "source": "World Bank via data/derived/market_indicators.csv"}


class SeriesStore:
    """Fetches (or synthesises offline) one pageview series per language, once per run."""

    def __init__(self, http: T.Http | None, titles: dict[str, str], trend: str, as_of: dt.date, offline: bool):
        self.http, self.titles, self.trend, self.as_of, self.offline = http, titles, trend, as_of, offline
        self.start = as_of - dt.timedelta(days=3 * 365)
        self.cache: dict[str, T.Series] = {}
        self.errors: dict[str, str] = {}

    def prefetch(self, langs: list[str]) -> None:
        todo = [lg for lg in dict.fromkeys(langs) if lg not in self.cache]
        if self.offline:
            for lg in todo:
                self.cache[lg] = T.offline_series(self.trend, lg, self.as_of) if lg in self.titles else []
            return
        with ThreadPoolExecutor(max_workers=6) as ex:
            for lg, s in zip(todo, ex.map(self._fetch, todo)):
                self.cache[lg] = s

    def _fetch(self, lang: str) -> T.Series:
        title = self.titles.get(lang)
        if not title:
            return []
        try:
            return T.fetch_pageviews(self.http, title, self.start, self.as_of, lang)
        except Exception as exc:  # one edition failing must not sink the run, but say why if all do
            self.errors[lang] = str(exc)
            return []

    def first_date(self) -> dt.date | None:
        dates = [s[0][0] for s in self.cache.values() if s]
        return min(dates) if dates else None

    def get(self, lang: str) -> T.Series:
        if lang not in self.cache:
            self.prefetch([lang])
        return self.cache[lang]


def market_signal(store: SeriesStore, langs: list[str], reference: dict) -> dict:
    """Combine the market's own-language editions; fall back to English only when none has data."""
    native = [lg for lg in langs if lg != "en"]
    used = [lg for lg in native if store.get(lg)]
    basis = "own_language"
    if not used:
        used = ["en"] if store.get("en") else []
        basis = "shared_english" if used else "none"
    series = T.combine([store.get(lg) for lg in used])
    report = T.analyze(series, store.as_of, reference) if series else {"stage": "insufficient_data", "points": 0}
    if report["stage"] == "insufficient_data" and basis == "own_language" and store.get("en"):
        used, basis = ["en"], "shared_english_fallback"
        report = T.analyze(store.get("en"), store.as_of, reference)
    return {"languages_requested": langs, "languages_used": used, "basis": basis,
            "titles": {lg: store.titles.get(lg) for lg in used}, **report}


def _rank01(values: list[float | None]) -> list[float]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return [0.0] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        elif len(present) == 1:
            out.append(1.0)
        else:
            out.append(round(sum(1 for p in present if p < v) / (len(present) - 1), 3))
    return out


def score_markets(rows: list[dict], weights: dict[str, float]) -> list[dict]:
    """Rank-normalise each component to 0..1 across markets, then a weighted sum. Components are returned."""
    comps = {
        "interest_momentum": [r["signal"].get("momentum_90d") for r in rows],
        # English pageviews are global, so their level says nothing about one country: not scored
        "interest_level": [r["signal"].get("current_views_smoothed")
                           if r["signal"].get("basis") == "own_language" else None for r in rows],
        "market_size": [r["indicators"]["gdp_usd"] or None for r in rows],
        "purchasing_power": [r["indicators"]["gdp_per_capita_usd"] for r in rows],
        "compliance_ease": [-(r["compliance_burden"]) for r in rows],
    }
    normed = {k: _rank01(v) for k, v in comps.items()}
    total_w = sum(weights.values()) or 1
    for i, r in enumerate(rows):
        r["score_components"] = {k: normed[k][i] for k in comps}
        r["score"] = round(sum(weights.get(k, 0) * normed[k][i] for k in comps) / total_w, 3)
    return sorted(rows, key=lambda r: -r["score"])
