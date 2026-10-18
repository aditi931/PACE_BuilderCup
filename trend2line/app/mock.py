"""MOCK_LLM=1 handlers: canned Gemini outputs so the team can demo without a key.

Specs come from data/demo_trends.yaml. For a trend not in that file, a deterministic fallback builds a
minimal spec from ingredient words found in the trend name (clearly labelled). The mock writer deliberately
makes mistakes on attempt 1 so the verifier loop is visible offline. Everything is labelled 'mock'.
"""
from .stages import catalog
from .stages.ingredients import taxonomy


def _demo(trend: str) -> dict | None:
    t = trend.strip().lower()
    return next((d for d in catalog.demo_trends() if d["trend"].lower() == t), None)


def deconstruct(prompt: str, ctx: dict) -> dict:
    trend = ctx.get("trend", "")
    if d := _demo(trend):
        return {**d["spec"], "notes": "MOCK spec from data/demo_trends.yaml."}
    spans = taxonomy().spans(trend)
    return {
        "product_name": trend,
        "category": "other",
        "format": "other",
        "hero_ingredient": spans[0][0] if spans else trend,
        "ingredients": [{"name": s, "role": "base", "components": []} for s, _ in spans] or
                       [{"name": trend, "role": "base", "components": []}],
        "process_steps": [],
        "notes": "MOCK fallback: ingredients guessed from words in the trend name; set a Gemini key for a real spec.",
    }


def pick_article(prompt: str, ctx: dict) -> dict:
    return {"title": ctx.get("title", ""), "reason": "mock"}


def write_plan(prompt: str, ctx: dict) -> dict:
    reg, state, plant, attempt = ctx["reg"], ctx["state"], ctx["plant"], ctx["attempt"]
    d = state["decision"]
    idx = state["evidence_index"]
    tl_id = idx.get(f"timeline_{d['route']}")
    tl = reg.get(tl_id)["payload"]
    cap = tl["capture"]["median"]
    claims = [
        {"text": f"Median capture at launch is {round(cap * 100)}%.", "kind": "numeric",
         "evidence_ids": [tl_id], "field": "capture.median", "value": round(cap * 100)},
        {"text": f"Time to shelf is {tl['total_days']} days.", "kind": "numeric",
         "evidence_ids": [tl_id], "field": "total_days", "value": tl["total_days"]},
        {"text": f"Earliest shelf date is {tl['launch_date']}.", "kind": "date",
         "evidence_ids": [tl_id], "field": "launch_date", "date_value": tl["launch_date"]},
        {"text": "Consumers may prefer a smaller pack size at first.", "kind": "speculative"},
    ]
    dl = reg.get(idx["deadline"])["payload"]
    if dl["go_by_days"]:
        claims.append({"text": f"To keep half of today's interest the product must be on shelf within "
                               f"{dl['go_by_days']['median']} days.", "kind": "numeric", "evidence_ids": [idx["deadline"]],
                       "field": "go_by_days.median", "value": dl["go_by_days"]["median"]})
        claims.append({"text": f"Shelf deadline {dl['go_by_date']['median']}.", "kind": "date",
                       "evidence_ids": [idx["deadline"]], "field": "go_by_date.median",
                       "date_value": dl["go_by_date"]["median"]})
    comp = reg.get(idx["compliance"])["payload"]
    if comp["triggered_rule_ids"]:
        claims.append({"text": "Allergen and labelling rules for this market apply.", "kind": "regulatory",
                       "rule_id": comp["triggered_rule_ids"][0]})
    if d["line"]:
        line = plant["lines"][d["line"]]
        cap_name = line["capabilities"][0]
        claims.append({"text": f"{d['line']} has {cap_name}.", "kind": "process",
                       "line": d["line"], "capability": cap_name, "capability_present": True})
    rationale = (f"{d['verdict']} for {comp['market_name']} via {d['route'].replace('_', ' ')}: about "
                 f"{round(cap * 100)}% of current interest remains after {tl['total_days']} days to shelf.")
    if dl["go_by_days"]:
        rationale += f" The product must be on shelf within {dl['go_by_days']['median']} days to keep half of it."
    if attempt == 1:  # deliberate errors so the verifier has something to catch offline
        wrong_line = next((lid for lid, ln in plant["lines"].items()
                           if "one_shot_depositing" not in ln["capabilities"]), None)
        if wrong_line:
            claims.append({"text": f"{wrong_line} can deposit filled bars.", "kind": "process",
                           "line": wrong_line, "capability": "one_shot_depositing", "capability_present": True})
        rationale += " Ingredient prices rose 60% this year."
    return {"headline": f"{d['verdict']}: {state['trend']} in {comp['market_name']}", "rationale": rationale,
            "actions": [f"Lock the route: {d['route'].replace('_', ' ')}.", "Start artwork in parallel with trials."],
            "claims": claims}


def discover_launches(prompt: str, ctx: dict) -> dict:
    """Tests only: no web access, so no launches. Never invents data."""
    return {"text": "[]", "sources": []}


def radar_classify(prompt: str, ctx: dict) -> dict:
    """Tests only: a term counts as food if it contains a word from a tiny fixed list."""
    food = ("chocolate", "coffee", "cake", "tea", "kunafa", "noodle", "boba", "mochi", "croffle")
    return {"items": [{"term": t, "is_food_trend": any(f in t.lower() for f in food),
                       "english_name": t if any(f in t.lower() for f in food) else ""} for t in ctx.get("terms", [])]}


MOCK_HANDLERS = {"deconstruct": deconstruct, "pick_article": pick_article, "write_plan": write_plan,
                 "discover_launches": discover_launches, "radar_classify": radar_classify}


def draft_label(prompt: str, ctx: dict) -> dict:
    """MOCK label: English wording whatever the language (the script check then fails for non-Latin markets)."""
    run = ctx["run"]
    labels = [d["label"] for d in run["compliance"]["declarations"]]
    names = ", ".join(i["name"] for i in run["spec"]["ingredients"])
    return {"product_name_local": run["spec"]["product_name"], "ingredients_statement": f"Ingredients: {names}.",
            "allergen_terms": [{"declaration": x, "local_term": x} for x in labels],
            "allergen_statement": "Contains: " + ", ".join(labels) + "." if labels else "",
            "back_translation": "MOCK draft (MOCK_LLM=1): not translated."}


def ask(prompt: str, ctx: dict) -> dict:
    """MOCK answer: restates the decision with one verifiable number."""
    run = ctx["run"]
    d, idx = run["decision"], run.get("evidence_index", {})
    tl = idx.get(f"timeline_{d['route']}")
    cap = round(d["capture_median"] * 100)
    return {"answer": f"MOCK answer: the verdict is {d['verdict']} because about {cap}% of today's interest is "
                      f"left when the product reaches shelf.",
            "claims": [{"text": f"{cap}% of today's interest is left at launch.", "kind": "numeric",
                        "evidence_ids": [tl] if tl else [], "field": "capture.median", "value": cap}]}


MOCK_HANDLERS.update({"draft_label": draft_label, "ask": ask})
