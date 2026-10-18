"""Build a plant profile for a REAL company from its PUBLIC product data in Open Food Facts.

    python -m scripts.build_company_profile --brand lindt --market EU
    python -m scripts.build_company_profile --brand amul --country india --market IN
    python -m scripts.build_company_profile --brand britannia --country india --market IN

What is real: the products, their ingredients, declared allergens, "may contain" traces and free-from labels,
all as recorded in Open Food Facts (ODbL) with a link to each product page.
What is INFERRED: the lines. A company's real lines are not public, so products are grouped by category using
data/category_lines.yaml, and every inferred line is named "(inferred)". "May contain" traces become line
allergens, because a trace warning is public evidence that the plant handles that allergen.

The output (data/companies/<brand>.yaml) can be uploaded in the UI as "Your plant file".
Open Food Facts asks for a descriptive User-Agent and limits searches to about 10 per minute.
"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import datetime as dt
import os
import time

import httpx
import yaml

from app.config import CONTACT_URL, DATA_DIR, get_settings, user_agent_with_contact
from app.stages.ingredients import taxonomy

SEARCH = "https://world.openfoodfacts.org/api/v2/search"
FIELDS = ("code,product_name,product_name_en,brands,ingredients_tags,ingredients_text,allergens_tags,traces_tags,"
          "labels_tags,categories_tags,manufacturing_places,countries_tags")


def fetch_products(brand: str, country: str | None, limit: int, user_agent: str) -> list[dict]:
    params = {"brands_tags": brand, "fields": FIELDS, "page_size": min(limit, 100), "sort_by": "unique_scans_n"}
    if country:
        params["countries_tags_en"] = country
    r = httpx.get(SEARCH, params=params, headers={"User-Agent": user_agent}, timeout=60)
    r.raise_for_status()
    return r.json().get("products", [])[:limit]


def _group_for(categories: list[str], groups: list[dict]) -> dict | None:
    cats = set(categories or [])
    return next((g for g in groups if cats & set(g["match"])), None)


def build_profile(products: list[dict], brand: str, market: str, label_languages: list[str], cfg: dict) -> dict:
    tx = taxonomy()
    groups, ff_map = cfg["groups"], cfg["free_from_labels"]
    lines: dict[str, dict] = {}
    skus: dict[str, dict] = {}
    skipped = {"no_ingredients": 0, "no_category_match": 0}
    for p in products:
        tags = [t for t in (p.get("ingredients_tags") or []) if t in tx.nodes]
        if not tags:
            skipped["no_ingredients"] += 1
            continue
        g = _group_for(p.get("categories_tags"), groups)
        if not g:
            skipped["no_category_match"] += 1
            continue
        seen, names = set(), []
        for t in tags:  # most specific first; drop parents already implied by a child
            label = tx.nodes[t].label
            if label not in seen:
                seen.add(label)
                names.append(label)
        free_from: list[str] = []
        for lab in p.get("labels_tags") or []:
            v = ff_map.get(lab)
            free_from += v if isinstance(v, list) else ([v] if v else [])
        line = lines.setdefault(g["id"], {"name": g["name"], "category": g["category"],
                                          "capabilities": list(g["capabilities"]), "extra_allergens": set(),
                                          "inferred": True})
        line["extra_allergens"] |= set(p.get("traces_tags") or []) | set(p.get("allergens_tags") or [])
        sid = p.get("code") or f"P{len(skus) + 1}"
        skus[sid] = {
            "name": (p.get("product_name_en") or p.get("product_name") or sid).strip(),
            "line": g["id"],
            "format": g["category"],
            "ingredients": names[:25],
            "process_steps": list(g["steps"]),
            "free_from": sorted(set(free_from)),
            "declared_allergens": sorted(p.get("allergens_tags") or []),
            "may_contain": sorted(p.get("traces_tags") or []),
            "manufacturing_places": p.get("manufacturing_places") or "",
            "source_url": f"https://world.openfoodfacts.org/product/{sid}",
        }
    for ln in lines.values():
        ln["extra_allergens"] = sorted(ln["extra_allergens"])
    return {
        "company": f"{brand} (public pack data; lines inferred)",
        "provenance": "public_pack_data_inferred_lines",
        "source": "Open Food Facts (ODbL), https://world.openfoodfacts.org",
        "built_on": dt.date.today().isoformat(),
        "home_market": market,
        "base_label_languages": label_languages,
        "data_quality": {"products_fetched": len(products), "products_used": len(skus), **skipped},
        "lines": lines,
        "skus": skus,
    }


def build_one(brand: str, country: str | None, market: str, languages: str, limit: int, cfg: dict, ua: str,
              note: str = "") -> int:
    products = fetch_products(brand, country, limit, ua)
    profile = build_profile(products, brand, market, languages.split(","), cfg)
    if note:
        profile["note"] = note
    out = DATA_DIR / "companies" / f"{brand}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    q = profile["data_quality"]
    if q["products_used"] == 0:
        print(f"{brand}: no usable products ({q}); nothing written")
        return 0
    out.write_text(yaml.safe_dump(profile, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"{out}: {q['products_used']} of {q['products_fetched']} products used "
          f"({q['no_ingredients']} without usable ingredients, {q['no_category_match']} outside known categories); "
          f"{len(profile['lines'])} inferred lines")
    return q["products_used"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", help="Open Food Facts brand tag, e.g. lindt, amul, britannia")
    ap.add_argument("--all", action="store_true", help="build every company in data/companies.yaml")
    ap.add_argument("--country", help="Open Food Facts country, e.g. india, germany")
    ap.add_argument("--market", default="IN", help="Market ID from data/markets.yaml")
    ap.add_argument("--languages", default="en", help="Label languages the company already prints, comma-separated")
    ap.add_argument("--limit", type=int, default=80)
    a = ap.parse_args()
    cfg = yaml.safe_load((DATA_DIR / "category_lines.yaml").read_text(encoding="utf-8"))
    ua = user_agent_with_contact(os.environ.get("WIKI_USER_AGENT", get_settings().wiki_user_agent), CONTACT_URL)
    if a.all:
        items = yaml.safe_load((DATA_DIR / "companies.yaml").read_text(encoding="utf-8"))["companies"]
        built = 0
        for i, c in enumerate(items):
            if i:
                time.sleep(7)  # Open Food Facts allows about 10 searches a minute
            try:
                built += bool(build_one(c["brand"], c.get("country"), c["market"], c.get("languages", "en"),
                                        a.limit, cfg, ua, c.get("note", "")))
            except Exception as exc:
                print(f"{c['brand']}: failed ({exc}); skipped")
        print(f"{built} of {len(items)} company profiles built")
        if not built:
            _sys.exit(2)
        return
    if not a.brand:
        ap.error("give --brand or --all")
    if not build_one(a.brand, a.country, a.market, a.languages, a.limit, cfg, ua):
        _sys.exit("No usable products; try another --brand tag or drop --country.")


if __name__ == "__main__":
    main()
