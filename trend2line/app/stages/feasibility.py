"""Line feasibility and the allergen blast radius. Set logic over the plant file, no LLM."""


def claim_conflicts(claim: str, allergens: set[str], lineage: set[str], claims_cfg: dict) -> list[str]:
    c = claims_cfg.get(claim)
    if not c:
        return []
    hits = set(c.get("allergens", [])) & allergens
    hits |= {n for n in c.get("nodes", []) if n in lineage}
    return sorted(hits)


def assess_line(line_id: str, line: dict, steps: list[str], product_allergens: set[str], product_lineage: set[str],
                process_map: dict[str, list[str]], catalog: dict[str, list[dict]], validation_days: int,
                claims_cfg: dict) -> dict:
    caps = set(line["capabilities"])
    gaps, seen = [], set()
    for step in steps:
        if step == "other":
            continue
        required = process_map.get(step)
        if required is None:
            gaps.append({"step": step, "required_any": [], "best_option": None, "unknown_step": True})
            continue
        if caps & set(required) or tuple(required) in seen:
            continue
        seen.add(tuple(required))
        options = sorted((o | {"capability": c} for c in required for o in catalog.get(c, [])),
                         key=lambda o: o["lead_time_days"])
        gaps.append({"step": step, "required_any": required,
                     "best_option": options[0] if options else None, "options": options})

    new_allergens = sorted(product_allergens - set(line["allergens_present"]))
    conflicts = []
    for sku_id, sku in (line.get("skus") or {}).items():
        for claim in sku.get("free_from", []):
            hit = claim_conflicts(claim, product_allergens, product_lineage, claims_cfg)
            if hit:
                conflicts.append({"sku_id": sku_id, "sku_name": sku["name"], "claim": claim,
                                  "claim_label": claims_cfg.get(claim, {}).get("label", claim), "allergens": hit})
    # allergens already on this line that the new product does not contain -> cross-contact risk for it
    cross_contact = sorted(set(line["allergens_present"]) - product_allergens)

    unresolvable = [g["step"] for g in gaps if g["best_option"] is None]
    gap_days = max((g["best_option"]["lead_time_days"] for g in gaps if g["best_option"]), default=0)
    needs_validation = bool(new_allergens)
    return {
        "line_id": line_id,
        "line_name": line.get("name", line_id),
        "category": line.get("category", ""),
        "capabilities": sorted(caps),
        "gaps": gaps,
        "gap_lead_days": gap_days,
        "unresolvable_steps": unresolvable,
        "new_allergens_on_line": new_allergens,
        "cross_contact_allergens": cross_contact,
        "needs_cleaning_validation": needs_validation,
        "free_from_conflicts": conflicts,
        "feasible": not unresolvable,
        "score": [int(bool(unresolvable)), len(conflicts), len(unresolvable),
                  gap_days + (validation_days if needs_validation else 0), len(new_allergens)],
    }


def rank_lines(assessments: list[dict]) -> list[dict]:
    """Feasible lines first, then fewest free-from conflicts, then fastest, then fewest new allergens."""
    return sorted(assessments, key=lambda a: a["score"])
