"""Loads the knowledge tables in data/. These are authoritative: the LLM never invents their contents."""
import csv
import json
from functools import lru_cache

import yaml

from ..config import DATA_DIR
from .ingredients import taxonomy


def _csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="", encoding="utf-8") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def _yaml(name: str) -> dict:
    return yaml.safe_load((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache
def default_plant() -> dict:
    return prepare_plant(_yaml("plant.yaml"))


@lru_cache
def companies() -> dict[str, dict]:
    """Real company profiles built from public data (data/companies/*.yaml), keyed by file name."""
    out = {}
    folder = DATA_DIR / "companies"
    for f in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            out[f.stem] = prepare_plant(yaml.safe_load(f.read_text(encoding="utf-8")))
        except Exception:
            continue  # a broken profile must never break the app
    return out


def plant() -> dict:  # kept for older callers
    return default_plant()


def prepare_plant(raw: dict) -> dict:
    """Validate a plant definition and compute each line's allergen/ingredient hazards from its SKUs."""
    for key in ("lines", "skus"):
        if not isinstance(raw.get(key), dict) or not raw[key]:
            raise ValueError(f"plant file needs a non-empty '{key}' mapping")
    tx = taxonomy()
    lines = {lid: {**ln, "skus": {}} for lid, ln in raw["lines"].items()}
    for lid, ln in lines.items():
        if not isinstance(ln.get("capabilities"), list):
            raise ValueError(f"line {lid} needs a 'capabilities' list")
    skus = {}
    for sid, s in raw["skus"].items():
        if s.get("line") not in lines:
            raise ValueError(f"SKU {sid} refers to unknown line {s.get('line')}")
        resolved = [tx.resolve(i) for i in s.get("ingredients", [])]
        sku = {**s, "free_from": s.get("free_from") or [], "resolved": resolved,
               # allergens from the ingredients, plus any the pack itself declares (real company profiles)
               "allergens": sorted({a for r in resolved for a in r["allergens"]} | set(s.get("declared_allergens") or [])),
               "lineage": sorted({n for r in resolved for n in r["lineage"]})}
        skus[sid] = sku
        lines[s["line"]]["skus"][sid] = {"name": s["name"], "free_from": sku["free_from"]}
    for lid, ln in lines.items():
        on_line = [skus[s] for s in ln["skus"]]
        ln["allergens_present"] = sorted({a for s in on_line for a in s["allergens"]} | set(ln.get("extra_allergens", [])))
        ln["lineage_present"] = sorted({n for s in on_line for n in s["lineage"]})
    return {**raw, "lines": lines, "skus": skus, "our_skus": skus}


@lru_cache
def timeline_cfg() -> dict:
    return _yaml("timeline.yaml")


@lru_cache
def scoring() -> dict:
    return _yaml("scoring.yaml")


@lru_cache
def markets() -> dict:
    return _yaml("markets.yaml")


@lru_cache
def free_from_claims() -> dict:
    return _yaml("free_from_claims.yaml")["claims"]


@lru_cache
def demo_trends() -> list[dict]:
    return _yaml("demo_trends.yaml")["trends"]


@lru_cache
def reference_launches() -> dict:
    p = DATA_DIR / "reference_launches.yaml"
    return _yaml("reference_launches.yaml") if p.exists() else {}


@lru_cache
def fad_reference() -> dict:
    return json.loads((DATA_DIR / "fad_reference.json").read_text(encoding="utf-8"))


@lru_cache
def manifest() -> dict:
    p = DATA_DIR / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@lru_cache
def process_map() -> dict[str, list[str]]:
    return {r["process_step"]: r["required_any"].split("|") for r in _csv("process_map.csv")}


@lru_cache
def categories() -> list[str]:
    """Product categories, taken from the category column of process_map.csv (plus 'other')."""
    cats = {r["category"] for r in _csv("process_map.csv") if r.get("category") and r["category"] != "shared"}
    return sorted(cats) + ["other"]


@lru_cache
def process_steps() -> list[str]:
    return list(process_map())


@lru_cache
def equipment_catalog() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in _csv("equipment_catalog.csv"):
        out.setdefault(r["capability"], []).append(
            {"option": r["option"], "lead_time_days": int(r["lead_time_days"]), "kind": r["kind"]}
        )
    return out


@lru_cache
def market_indicators() -> dict[str, dict]:
    out = {}
    for r in _csv("derived/market_indicators.csv"):
        for k in ("population", "gdp_usd", "gdp_per_capita_usd"):
            r[k] = float(r[k]) if r[k] else None
        out[r["iso2"]] = r
    return out


@lru_cache
def place_names() -> dict[str, str]:
    """Lower-cased place name -> what it is. Countries (CLDR + official names) and GeoNames cities >15k."""
    out: dict[str, str] = {}
    for r in _csv("sources/world_cities.csv"):
        n = r["name"].lower()
        if len(n) >= 4:
            out.setdefault(n, f"city in {r['country']}")
    for r in _csv("sources/country_codes.csv"):
        for n in (r["CLDR display name"], r["official_name_en"], r["UNTERM English Short"]):
            n = (n or "").lower().replace("the ", "").strip()
            if len(n) >= 4:
                out[n] = "country"
    return out
