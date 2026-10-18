"""Download every real dataset listed in data/sources.yaml and rebuild derived tables.

Usage (from the repo root, needs internet):
    python -m scripts.refresh_data            # download + rebuild everything
    python -m scripts.refresh_data --offline  # rebuild derived tables from files already in data/sources/

Writes data/manifest.json with URL, licence, byte size, sha256 and fetch time for each file, so the
UI and the judges can see exactly where every number came from.
"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):  # Windows consoles: print non-English titles safely
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys

import httpx
import yaml

from app.config import DATA_DIR

UA = "Trend2Line/0.2 (data refresh; set WIKI_USER_AGENT with a contact)"


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(sources: list[dict], offline: bool) -> list[dict]:
    rows = []
    for s in sources:
        path = DATA_DIR / s["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {k: s[k] for k in ("id", "url", "file", "publisher", "licence", "used_for")}
        if not offline:
            try:
                r = httpx.get(s["url"], headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
                r.raise_for_status()
                path.write_bytes(r.content)
                entry["fetched_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            except Exception as exc:  # keep going; report at the end
                entry["error"] = str(exc)
        if path.exists():
            entry["bytes"] = path.stat().st_size
            entry["sha256"] = _sha256(path)
            entry["status"] = "ok" if "error" not in entry else "stale_copy"
        else:
            entry["status"] = "missing"
        rows.append(entry)
        print(f"{entry['status']:>10}  {s['id']}  {entry.get('bytes', 0):>9} bytes")
    return rows


def _latest(path, code_col: str, year_col: str, value_col: str) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.setdefault(r[code_col], {})[int(r[year_col])] = float(r[value_col])
            except (ValueError, KeyError):
                continue
    return out


def build_market_indicators() -> int:
    """One row per country: ISO codes, region, languages, latest population, GDP and GDP per capita."""
    src = DATA_DIR / "sources"
    pop = _latest(src / "wb_population.csv", "Country Code", "Year", "Value")
    gdp = _latest(src / "wb_gdp.csv", "Country Code", "Year", "Value")
    out = DATA_DIR / "derived" / "market_indicators.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(src / "country_codes.csv", newline="", encoding="utf-8") as f, \
            open(out, "w", newline="", encoding="utf-8") as g:
        w = csv.writer(g)
        w.writerow(["iso2", "iso3", "name", "region", "subregion", "languages", "population", "population_year",
                    "gdp_usd", "gdp_year", "gdp_per_capita_usd", "gdp_per_capita_year"])
        for r in csv.DictReader(f):
            iso2, iso3 = r["ISO3166-1-Alpha-2"], r["ISO3166-1-Alpha-3"]
            if not iso2 or not iso3:
                continue
            p, g_ = pop.get(iso3, {}), gdp.get(iso3, {})
            py = max(p) if p else None
            gy = max(g_) if g_ else None
            common = sorted(set(p) & set(g_))
            cy = common[-1] if common else None
            w.writerow([iso2, iso3, r["CLDR display name"] or r["official_name_en"], r["Region Name"],
                        r["Sub-region Name"], r["Languages"],
                        int(p[py]) if py else "", py or "",
                        round(g_[gy]) if gy else "", gy or "",
                        round(g_[cy] / p[cy]) if cy and p[cy] else "", cy or ""])
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="skip downloads, rebuild derived tables only")
    args = ap.parse_args()
    sources = yaml.safe_load((DATA_DIR / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    rows = download(sources, args.offline)
    n = build_market_indicators()
    print(f"derived/market_indicators.csv: {n} countries")
    manifest_path = DATA_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest["sources"] = rows
    manifest["derived"] = {"market_indicators": {"file": "derived/market_indicators.csv", "rows": n,
                                                 "built_from": ["wb_population", "wb_gdp", "country_codes"]}}
    manifest["refreshed_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if any(r["status"] == "missing" for r in rows):
        sys.exit("Some sources are missing; see above.")


if __name__ == "__main__":
    main()
