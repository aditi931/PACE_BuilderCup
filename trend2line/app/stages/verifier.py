"""Typed claim verification. Code checks first; anything it cannot check is an assumption."""
import re

from ..evidence import EvidenceRegistry
from ..models import Plan

NUM_RE = re.compile(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(%|percent\b|days?\b|weeks?\b)", re.I)
NO_NEW_ALLERGENS_RE = re.compile(r"\bno (?:new )?allergens?\b", re.I)
WEIGHTS = {"regulatory": 3, "numeric": 2, "date": 2, "process": 2, "orphan": 2, "text": 1}


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.01 * abs(b), 0.005)


def _matches(value: float, target: float) -> bool:
    """A share (0..1) may be written as a percentage, rounded to a whole number: 0.204 -> '20%'."""
    return _close(value, target) or (abs(target) <= 1 and (_close(value, target * 100)
                                                           or (abs(value) >= 1 and abs(value - target * 100) <= 0.5)))


def check_claim(c, reg: EvidenceRegistry, plant: dict, compliance: dict) -> tuple[str, str, bool]:
    """Returns (status, reason, hard_failure)."""
    if c.kind == "speculative":
        return "assumption", "No evidence can verify this; shown as an assumption.", False

    if c.kind == "process":
        line = plant["lines"].get(c.line or "")
        if line is None or c.capability is None or c.capability_present is None:
            return "failed", "Process claim needs a known line, a capability and capability_present.", False
        actual = c.capability in line["capabilities"]
        if actual != c.capability_present:
            return "failed", f"plant.yaml: {c.line} {'has' if actual else 'lacks'} {c.capability}.", True
        return "verified", "Matches plant.yaml.", False

    if c.kind == "regulatory":
        if c.rule_id not in compliance["triggered_rule_ids"]:
            return "failed", f"Rule {c.rule_id} does not exist or was not triggered.", True
        if c.rule_id in compliance["unverified_rule_ids"]:
            return "verified", "Rule triggered; its source is still pending verification.", False
        return "verified", "Rule triggered.", False

    if not c.evidence_ids or not c.field:
        return "failed", "No evidence ID or field cited.", False
    last = "No cited evidence matched."
    for eid in c.evidence_ids:
        try:
            target = reg.lookup(eid, c.field)
        except KeyError as exc:
            last = str(exc)
            continue
        if c.kind == "numeric":
            if c.value is None or not isinstance(target, (int, float)):
                last = f"{eid}.{c.field} is not numeric or claim has no value."
                continue
            if _matches(c.value, float(target)):
                return "verified", f"{eid}.{c.field} = {target}.", False
            last = f"Evidence says {target}; claim says {c.value}."
        elif c.kind == "date":
            if c.date_value == target:
                return "verified", f"{eid}.{c.field} = {target}.", False
            last = f"Recomputed date is {target}; claim says {c.date_value}."
    return "failed", last, False


def verify(plan: Plan, reg: EvidenceRegistry, plant: dict, compliance: dict, bom: dict) -> dict:
    results, hard = [], []
    allowed: list[float] = []
    for c in plan.claims:
        status, reason, is_hard = check_claim(c, reg, plant, compliance)
        results.append({**c.model_dump(), "status": status, "reason": reason, "hard_failure": is_hard})
        if is_hard:
            hard.append(f"{c.text} — {reason}")
        if status == "verified" and c.kind == "numeric" and c.value is not None:
            allowed.append(c.value)

    prose = " ".join([plan.headline, plan.rationale, *plan.actions])
    orphans = []
    for m in NUM_RE.finditer(prose):
        n = float(m.group(1))
        if not any(_matches(n, a) for a in allowed):
            orphans.append({"text": m.group(0), "status": "failed",
                            "reason": "Number in prose without a verified claim behind it."})

    if NO_NEW_ALLERGENS_RE.search(prose) and bom["new_allergens_vs_our_sku"]:
        hard.append("Prose says no new allergens, but the BOM adds: "
                    + ", ".join(bom["new_allergens_vs_our_sku"]))

    num = den = 0
    for r in results:
        if r["status"] == "assumption":
            continue
        w = WEIGHTS.get(r["kind"], 1)
        den += w
        num += w if r["status"] == "verified" else 0
    den += WEIGHTS["orphan"] * len(orphans)
    trust = round(num / den, 3) if den else 0.0
    return {"claims": results, "orphans": orphans, "hard_failures": hard, "trust": trust,
            "passed": not hard and trust >= 0.9}


def feedback_for_writer(ver: dict) -> str:
    lines = [f"- [{c['kind']}] {c['text']}: {c['reason']}" for c in ver["claims"] if c["status"] == "failed"]
    lines += [f"- [orphan] '{o['text']}': {o['reason']}" for o in ver["orphans"]]
    lines += [f"- [hard] {h}" for h in ver["hard_failures"]]
    return "\n".join(lines)
