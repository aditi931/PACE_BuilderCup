"""Compliance from data/markets.yaml. Rule text and sources come from the file, never from the LLM."""
import datetime as dt

from .ingredients import norm, taxonomy


def _item_matches(item: dict, allergens: set[str], lineage: set[str]) -> list[str]:
    hits = sorted(set(item.get("allergens", [])) & allergens)
    hits += [n for n in item.get("nodes", []) if n in lineage]
    return hits


def _active(item: dict, on: dt.date) -> bool:
    eff = item.get("effective_from")
    if not eff:
        return True
    return on >= (eff if isinstance(eff, dt.date) else dt.date.fromisoformat(str(eff)))


def find_place_names(text: str, places: dict[str, str]) -> list[dict]:
    """Place names in a product/trend name, ignoring words that belong to an ingredient ('Brazil nut')."""
    tx = taxonomy()
    words = norm(text).split()
    ingredient_words = {w for span, _ in tx.spans(text) if len(span.split()) > 1 for w in span.split()}
    found, used = [], set()
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            if any(k in used for k in range(i, i + size)):
                continue
            span = " ".join(words[i:i + size])
            if span in places and not (set(span.split()) & ingredient_words):
                if size == 1 and tx.lookup(span)[1] in ("exact", "variant"):
                    continue  # 'orange', 'mango' etc. are ingredients first
                found.append({"term": span, "kind": places[span]})
                used.update(range(i, i + size))
    return found


def evaluate(market_id: str, markets_cfg: dict, product_allergens: list[str], product_lineage: list[str],
             product_text: str, line: dict | None, launch_date: dt.date, base_label_languages: list[str],
             places: dict[str, str]) -> dict:
    m = markets_cfg["markets"].get(market_id)
    if m is None:
        raise ValueError(f"Unknown market {market_id}")
    tx = taxonomy()
    allergens, lineage = set(product_allergens), set(product_lineage)

    declarations, upcoming, covered = [], [], set()
    for item in m.get("allergens", []):
        hits = _item_matches(item, allergens, lineage)
        if not hits:
            continue
        row = {"id": item["id"], "label": item["label"], "matched_by": hits,
               "effective_from": str(item.get("effective_from", "")) or None}
        (declarations if _active(item, launch_date) else upcoming).append(row)
    recommended = []
    for item in m.get("recommended", []):
        hits = _item_matches(item, allergens, lineage)
        if hits and _active(item, launch_date):
            recommended.append({"id": item["id"], "label": item["label"], "matched_by": hits})
    for d in declarations + recommended + upcoming:
        for h in d["matched_by"]:
            if h in allergens:
                covered.add(h)
            elif h in tx.nodes:  # a node match (walnut) covers that node's own allergen tags (nuts)
                covered |= tx.allergens_of(h) & allergens
    not_regulated = sorted(allergens - covered)

    label_needed = [lang for lang in m.get("label_languages", []) if lang not in base_label_languages]
    places_found = find_place_names(product_text, places)
    line = line or {}
    conflicts = line.get("free_from_conflicts", [])
    cross_contact = line.get("cross_contact_allergens", [])
    line_claims = {c for s in (line.get("skus_on_line") or {}).values() for c in s.get("free_from", [])}

    triggered = []
    for r in m.get("rules", []):
        t = r["trigger"]
        typ = t["type"]
        detail, hit = "", False
        if typ == "always":
            hit = True
        elif typ == "allergen_declared":
            hit = bool(declarations)
            detail = ", ".join(d["label"] for d in declarations)
        elif typ == "recommended_present":
            hit = bool(recommended)
            detail = ", ".join(d["label"] for d in recommended)
        elif typ == "cross_contact_on_line":
            hit = bool(cross_contact)
            detail = ", ".join(tx.allergen_label(a) for a in cross_contact)
        elif typ == "free_from_conflict":
            hit = bool(conflicts)
            detail = "; ".join(f"{c['sku_name']}: {c['claim_label']}" for c in conflicts)
        elif typ == "free_from_claim_on_shared_line":
            want = t.get("claim")
            hit = (want in line_claims) if want else bool(line_claims)
            detail = ", ".join(sorted(line_claims))
        elif typ == "place_name_in_product_name":
            hit = bool(places_found)
            detail = ", ".join(f"{p['term']} ({p['kind']})" for p in places_found)
        elif typ == "label_language":
            hit = bool(label_needed)
            detail = ", ".join(label_needed)
        if hit:
            s = r["source"]
            triggered.append({"rule_id": r["rule_id"], "trigger_type": typ, "requirement": r["requirement"],
                              "detail": detail, "source_title": s["title"], "source_url": s["url"],
                              "status": s["status"], "verified": s["status"] == "verified_official"})
    src = m["allergen_source"]
    return {
        "market": market_id,
        "market_name": m["name"],
        "regulator": m.get("regulator", ""),
        "allergen_list_source": {k: str(v) for k, v in src.items()},
        "declarations": declarations,
        "upcoming_declarations": upcoming,
        "recommended_declarations": recommended,
        "allergens_to_declare": [d["label"] for d in declarations],
        "n_mandatory_declarations": len(declarations),
        "allergens_not_regulated_here": [{"id": a, "label": tx.allergen_label(a)} for a in not_regulated],
        "label_languages_needed": label_needed,
        "place_names_found": places_found,
        "triggered_rule_ids": [r["rule_id"] for r in triggered],
        "triggered_rules": triggered,
        "unverified_rule_ids": [r["rule_id"] for r in triggered if not r["verified"]],
    }
