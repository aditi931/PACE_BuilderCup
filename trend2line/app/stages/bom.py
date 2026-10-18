"""BOM Morph: resolve every ingredient through the Open Food Facts taxonomy and diff against our SKU."""
from ..models import ProductSpec
from .ingredients import taxonomy

APPROXIMATE = {"partial", "components", "other_language", "unresolved"}


def resolve_spec(spec: ProductSpec) -> list[dict]:
    tx = taxonomy()
    return [{**tx.resolve(i.name, i.components), "role": i.role} for i in spec.ingredients]


def bom_diff(our_sku: dict, spec: ProductSpec, resolved: list[dict], known_steps: list[str]) -> dict:
    ours_labels = {lbl for r in our_sku["resolved"] for lbl in r["labels"]}
    theirs_labels = {lbl for r in resolved for lbl in r["labels"]}
    product_allergens = sorted({a for r in resolved for a in r["allergens"]})
    lineage = sorted({n for r in resolved for n in r["lineage"]})
    review = sorted({a for r in resolved for a in r["review_flags"]} - set(product_allergens))
    return {
        "our_sku": our_sku["name"],
        "resolved_ingredients": resolved,
        "added_ingredients": sorted(theirs_labels - ours_labels),
        "removed_ingredients": sorted(ours_labels - theirs_labels),
        "kept_ingredients": sorted(ours_labels & theirs_labels),
        "unmapped_ingredients": [r["name"] for r in resolved if r["match"] == "unresolved"],
        "approximate_ingredients": [{"name": r["name"], "match": r["match"]} for r in resolved
                                    if r["match"] in APPROXIMATE - {"unresolved"}],
        "allergens_from_components": sorted({a for r in resolved for a in r.get("allergens_from_components", [])}),
        "allergen_review_flags": review,
        "product_allergens": product_allergens,
        "product_lineage": lineage,
        "new_allergens_vs_our_sku": sorted(set(product_allergens) - set(our_sku["allergens"])),
        "format_from": our_sku["format"],
        "format_to": spec.format,
        "format_change": our_sku["format"] != spec.format,
        "new_process_steps": sorted(set(spec.process_steps) - set(our_sku["process_steps"])),
        "unknown_process_steps": sorted(set(spec.process_steps) - set(known_steps)),
    }
