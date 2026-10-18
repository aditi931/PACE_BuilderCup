"""Orchestrates the stages. LLM interprets (article, product spec, narrative); code computes everything else."""
import datetime as dt
import time

from . import prompts
from .config import Settings
from .evidence import EvidenceRegistry
from .llm import LLM, LLMError
from .models import ArticleChoice, Plan, ProductSpec
from .stages import bom as bom_stage
from .stages import catalog, compliance as compliance_stage, discovery, feasibility, references, signals, timeline
from .stages import trend as trend_stage
from .stages.verifier import feedback_for_writer, verify

class PipelineError(ValueError):
    pass


def _resolve_article(trend: str, market_langs: list[str], http: trend_stage.Http, llm: LLM, log) -> tuple[str, str, dict]:
    """Find the article (English first, else the market's own languages) and all its language editions."""
    for lang in ["en"] + [lg for lg in market_langs if lg != "en"]:
        try:
            candidates = trend_stage.search_candidates(http, trend, lang)
        except Exception as exc:
            raise PipelineError(f"Wikipedia search failed: {exc}") from exc
        if not candidates:
            continue
        title = candidates[0]
        if len(candidates) > 1:
            choice = llm.structured("pick_article", prompts.pick_article(trend, candidates), ArticleChoice,
                                    mock_context={"title": candidates[0]})
            title = choice.title if choice.title in candidates else candidates[0]
            log("Trend Scout", f"Gemini chose '{title}' from {len(candidates)} {lang} candidates.", "llm")
        try:  # other language editions are a bonus; the article itself is enough to continue
            qid = trend_stage.wikidata_qid(http, title, lang)
            titles = trend_stage.sitelinks(http, qid) if qid else {}
        except Exception as exc:
            qid, titles = None, {}
            try:
                titles = trend_stage.langlinks(http, title, lang)
                log("Trend Scout", f"Wikidata unavailable ({exc}); read {len(titles)} language links from Wikipedia.")
            except Exception as exc2:
                log("Trend Scout", f"Language links unavailable ({exc2}); using {lang} only.")
        titles.setdefault(lang, title)
        return title, qid or "", titles
    raise PipelineError(f"No Wikipedia article found for '{trend}' in {['en'] + market_langs}.")


def provenance(settings: Settings, plant: dict, spec_source: str, trend_source: str) -> list[dict]:
    m = catalog.manifest()
    fetched = {s["id"]: s.get("fetched_at", "") for s in m.get("sources", [])}
    ref = catalog.fad_reference()
    return [
        {"input": "Trend interest", "status": "synthetic" if settings.offline else "real_open_data",
         "detail": trend_source},
        {"input": "Fad half-life reference", "status": "real_open_data" if ref.get("status") == "computed" else "placeholder",
         "detail": f"computed {ref.get('computed_on', '-')}" if ref.get("status") == "computed" else "run scripts.build_fad_reference"},
        {"input": "Ingredients and allergens", "status": "real_open_data",
         "detail": f"Open Food Facts taxonomy, fetched {fetched.get('off_ingredients_taxonomy', '-')}"},
        {"input": "Market regulations", "status": "researched_regulation",
         "detail": "data/markets.yaml; each rule shows its source and verification status"},
        {"input": "Market size", "status": "real_open_data",
         "detail": f"World Bank population and GDP, fetched {fetched.get('wb_population', '-')}"},
        {"input": "Place names", "status": "real_open_data", "detail": "GeoNames cities and ISO country names"},
        {"input": "Plant, lines and SKUs", "status": plant.get("provenance", "company_input"),
         "detail": plant.get("company", "")},
        {"input": "Timeline durations and equipment lead times", "status": catalog.timeline_cfg().get("provenance", "company_input"),
         "detail": "data/timeline.yaml, data/equipment_catalog.csv"},
        {"input": "Market scan weights", "status": "company_input", "detail": "data/scoring.yaml"},
        {"input": "Real reference launches", "status": "real_public_record",
         "detail": "data/reference_launches.yaml: press releases, trade press and retailer listings, each linked"},
        {"input": "Launches found live", "status": "ai_discovered" if settings.grounding and not settings.offline else "off",
         "detail": "Gemini with Google Search grounding; each row validated by code and linked to web sources"},
        {"input": "Product spec", "status": spec_source,
         "detail": ("Reviewed demo spec (data/demo_trends.yaml); code resolves allergens" if spec_source == "curated_spec"
                    else "Gemini interprets the trend; code resolves allergens")},
    ]


def run(trend: str, sku_id: str, as_of: dt.date, market: str, image: tuple[bytes, str] | None,
        llm: LLM, settings: Settings, plant: dict | None = None, scan: bool = True, quick: bool = False) -> dict:
    """quick=True (compare view, opportunity watch): same decision, without live launch discovery or the
    Gemini write-up, so many runs stay fast and cheap. The verdict never depends on either."""
    t0 = time.time()
    steps: list[dict] = []
    reg = EvidenceRegistry()
    plant = plant or catalog.default_plant()
    our_sku = plant["skus"].get(sku_id)
    if our_sku is None:
        raise PipelineError(f"Unknown SKU {sku_id}")
    mcfg = catalog.markets()
    if market not in mcfg["markets"]:
        raise PipelineError(f"Unknown market {market}")
    indicators = catalog.market_indicators()
    aliases = mcfg.get("language_aliases", {})
    langs_by_market = {mid: signals.market_languages(m, indicators, aliases) for mid, m in mcfg["markets"].items()}
    reference = catalog.fad_reference()

    def log(name: str, detail: str, kind: str = "code") -> None:
        steps.append({"step": name, "detail": detail, "kind": kind, "t": round(time.time() - t0, 2)})

    # 1. Trend Scout ---------------------------------------------------------
    http = None
    if settings.offline:
        title, qid = trend, ""
        titles = {lg: trend for ls in langs_by_market.values() for lg in ls} | {"en": trend}
        trend_source = "OFFLINE: synthetic curves for tests, not real data"
        log("Trend Scout", trend_source)
    else:
        http = trend_stage.Http(settings.wiki_user_agent, settings.cache_dir, settings.http_cache_hours)
        title, qid, titles = _resolve_article(trend, langs_by_market[market], http, llm, log)
        trend_source = f"Wikimedia pageviews, {len(titles)} language editions via Wikidata {qid or '(no QID)'}"
        log("Trend Scout", f"'{title}' ({qid}) exists in {len(titles)} Wikipedia languages.")
    store = signals.SeriesStore(http, titles, trend, as_of, settings.offline)
    store.prefetch(sorted({lg for ls in (langs_by_market.values() if scan else [langs_by_market[market]]) for lg in ls} | {"en"}))
    sig = signals.market_signal(store, langs_by_market[market], reference)
    if sig["stage"] == "insufficient_data":
        if store.errors:
            raise PipelineError(f"Wikipedia pageviews could not be read for '{title}': {next(iter(store.errors.values()))}")
        first = store.first_date()
        hint = (f" Its pageview history starts on {first}; pick an 'as of' date at least 90 days later."
                if first and first <= as_of else " The article has no pageviews before the 'as of' date; pick a later date.")
        raise PipelineError(f"Not enough pageview history for '{title}' in {market} or English.{hint}")
    ev_trend = reg.record("wikimedia_pageviews" if not settings.offline else "synthetic_offline_curve",
                          f"{title} [{market}]", {"article": title, "wikidata": qid, "market": market, **sig})
    log("Trend Scout", f"{market}: stage {sig['stage']}, momentum {sig['momentum_90d']} "
                       f"from {sig['languages_used']} ({sig['basis']}).")

    # 2. Product Deconstructor ----------------------------------------------
    step_vocab = catalog.process_steps()
    curated = catalog.demo_trend(trend) if image is None else None
    if curated:  # a demo trend has a spec reviewed by a person: demo verdicts stay reproducible
        spec = ProductSpec.model_validate({**curated["spec"], "notes": "Reviewed spec from data/demo_trends.yaml."})
        spec_source = "curated_spec"
        ev_spec = reg.record("curated_product_spec", trend, spec.model_dump())
        log("Product Deconstructor", f"{spec.product_name}: reviewed demo spec, {len(spec.ingredients)} ingredients.")
    else:
        spec = llm.structured("deconstruct", prompts.deconstruct(trend, step_vocab, catalog.categories()), ProductSpec,
                              image=image, mock_context={"trend": trend})
        spec_source = "mock_llm" if settings.mock_llm else "gemini"
        if settings.mock_llm and "fallback" in spec.notes.lower():
            spec_source = "mock_fallback"
        ev_spec = reg.record("gemini_product_spec" if not settings.mock_llm else "mock_product_spec", trend,
                             spec.model_dump())
        log("Product Deconstructor", f"{spec.product_name}: {len(spec.ingredients)} ingredients.", "llm")

    # 3. BOM Morph (Open Food Facts taxonomy) -------------------------------
    resolved = bom_stage.resolve_spec(spec)
    bom = bom_stage.bom_diff(our_sku, spec, resolved, step_vocab)
    ev_bom = reg.record("bom_diff", sku_id, bom)
    log("BOM Morph", f"Allergens {bom['product_allergens']}; unresolved {bom['unmapped_ingredients'] or 'none'}.")

    # 4. Line feasibility + allergen blast radius ---------------------------
    cfg = catalog.timeline_cfg()
    claims_cfg = catalog.free_from_claims()
    pa, pl = set(bom["product_allergens"]), set(bom["product_lineage"])
    assessments = []
    for lid, line in plant["lines"].items():
        a = feasibility.assess_line(lid, line, list(spec.process_steps), pa, pl, catalog.process_map(),
                                    catalog.equipment_catalog(), cfg["in_house"]["allergen_cleaning_validation"],
                                    claims_cfg)
        a["skus_on_line"] = line.get("skus", {})
        assessments.append(a)
    ranked = feasibility.rank_lines(assessments)
    best = ranked[0]
    ev_lines = reg.record("plant", "line assessment",
                          {"recommended_line": best["line_id"], "lines": {a["line_id"]: a for a in ranked}})
    blast = [c | {"line_id": a["line_id"]} for a in assessments for c in a["free_from_conflicts"]]
    log("Line Feasibility", f"Best line {best['line_id']}; {len(blast)} free-from conflicts across lines.")

    # 5. Timeline and launch window -------------------------------------------
    m = mcfg["markets"][market]
    needs_l10n = any(lg not in plant.get("base_label_languages", []) for lg in m.get("label_languages", []))
    hl = sig["reference_half_life_days"]
    sustained = sig["stage"] == "sustained"

    # Launch deadline from public data alone: works even when the plant data is only a sample
    deadline = trend_stage.shelf_deadline(hl, as_of, cfg["decision"]["go_capture"],
                                          cfg["decision"]["conditional_capture"], sustained)
    ev_deadline = reg.record("shelf_deadline", f"{title} [{market}]", deadline)
    if sustained:
        log("Shelf Deadline", "Interest is sustained: no launch deadline from the fad curve.")
    else:
        log("Shelf Deadline", f"On shelf within {deadline['go_by_days']['median']} days "
                              f"(by {deadline['go_by_date']['median']}) to keep {round(deadline['go_share'] * 100)}% of interest.")

    # Real reference launches: curated (hand-checked) + discovered live with Gemini + Google Search
    ref_key, ref_entry = references.match(trend, catalog.reference_launches())
    curated = list((ref_entry or {}).get("launches", []))
    discovered_info = None
    if settings.grounding and not settings.offline and not quick:
        try:
            discovered_info = discovery.discover(trend, mcfg["markets"], llm, as_of, curated)
            log("Launch Discovery", f"Gemini + Google Search: {len(discovered_info['launches'])} launches accepted, "
                                    f"{len(discovered_info['rejected'])} rejected by code checks, "
                                    f"{len(discovered_info['grounding_sources'])} web sources.", "llm")
        except LLMError as exc:
            log("Launch Discovery", f"skipped: {exc}", "llm")
    found = (discovered_info or {}).get("launches", [])
    ref_rows, ref_windows, bench, mcheck, bench_source = [], {}, None, [], None
    if curated or found:
        entry = {"origin": (ref_entry or {}).get("origin"), "launches": curated + found}
        if not entry["origin"]:
            entry.pop("origin")
        ref_rows, ref_windows = references.evaluate(entry, store, langs_by_market, as_of)
        bench = references.benchmark([r for r in ref_rows if r.get("verification") != "ai_discovered"])
        bench_source = "curated" if bench else None
        if not bench:
            bench = references.benchmark(ref_rows)
            bench_source = "ai_discovered" if bench else None
        mcheck = references.model_check(ref_windows, reference)
        ref_key = ref_key or trend
        log("Reference Launches", f"{len(ref_rows)} real launches ({len(found)} found live); observed time to market "
                                  f"{'median ' + str(bench['median']) + ' days from ' + str(bench['n']) + ' launches' if bench else 'not enough data'}.")

    def route_eval(route: str, line: dict | None) -> dict:
        tasks = timeline.build_route(route, cfg, line["gap_lead_days"] if line else 0,
                                     bool(line and line["needs_cleaning_validation"]), needs_l10n)
        sched = timeline.schedule(tasks)
        cap = {k: (1.0 if sustained else trend_stage.capture_share(sched["total_days"], v)) for k, v in hl.items()}
        return {"route": route, "line": line["line_id"] if line else None, **sched,
                "launch_date": timeline.launch_date(as_of, sched["total_days"]), "capture": cap}

    routes = []
    if best["feasible"]:
        routes.append(route_eval("in_house", best))
    routes.append(route_eval("co_manufacturer", None))
    # The fastest line may be one that would void existing free-from claims. Show it, never choose it.
    feasible = [a for a in assessments if a["feasible"]]
    fastest = min(feasible, key=lambda a: a["score"][3], default=None)
    if fastest and fastest["free_from_conflicts"] and fastest["line_id"] != best["line_id"] \
            and fastest["score"][3] < best["score"][3]:
        blocked = route_eval("in_house_blocked", fastest)
        blocked["blocked_by"] = sorted({f"{c['sku_name']}: {c['claim_label']}" for c in fastest["free_from_conflicts"]})
        routes.append(blocked)
    if bench:  # real companies' observed time to market replaces sample durations
        days = int(round(bench["median"]))
        routes.append({"route": "observed_benchmark", "line": None, "total_days": days,
                       "critical_path": ["observed_time_to_market"],
                       "tasks": [{"task": "observed_time_to_market", "start_day": 0, "end_day": days,
                                  "duration_days": days, "critical": True}],
                       "launch_date": timeline.launch_date(as_of, days), "based_on": bench,
                       "capture": {k: (1.0 if sustained else trend_stage.capture_share(days, v)) for k, v in hl.items()}})
    route_ids = {r["route"]: reg.record("timeline", r["route"], r) for r in routes}

    # Decision (code, not LLM) -------------------------------------------------
    plant_status = plant.get("provenance", "company_input")
    # demo_company_input: a synthetic company's full plant file, used the way a real company's upload would be
    own_plant = plant_status in ("company_input", "uploaded_company_input", "demo_company_input")
    sample_plant = plant_status == "sample_company_input"
    hard = []
    if best["free_from_conflicts"] and not sample_plant:
        hard.append(f"Best in-house line {best['line_id']} would void free-from claims.")
    thresholds = cfg["decision"]
    in_house = next((r for r in routes if r["route"] == "in_house"), None)
    co_man = next(r for r in routes if r["route"] == "co_manufacturer")
    blocked_route = next((r for r in routes if r["route"] == "in_house_blocked"), None)
    margin = thresholds.get("prefer_in_house_within", 0.10)
    bench_route = next((r for r in routes if r["route"] == "observed_benchmark"), None)
    if bench_route and not own_plant:
        chosen = bench_route
        basis = (f"observed: median time to market of {bench['n']} real launches "
                 f"({', '.join(bench['companies'])})")
    else:
        basis = ("plant file of a synthetic demo company" if plant_status == "demo_company_input"
                 else "your plant data" if own_plant else f"{plant_status.replace('_', ' ')} (illustrative)")
        use_in_house = in_house and not hard and co_man["capture"]["median"] - in_house["capture"]["median"] <= margin
        chosen = in_house if use_in_house else co_man
    launch = dt.date.fromisoformat(chosen["launch_date"])

    # 6. Compliance for the target market (at the launch date) ---------------
    product_text = f"{trend} {spec.product_name}"
    comp = compliance_stage.evaluate(market, mcfg, bom["product_allergens"], bom["product_lineage"], product_text,
                                     best if chosen["route"] == "in_house" else None, launch,
                                     plant.get("base_label_languages", []), catalog.place_names())
    ev_comp = reg.record("markets_yaml", market, comp)
    log("Compliance", f"{market}: {comp['n_mandatory_declarations']} mandatory declarations, "
                      f"{len(comp['triggered_rule_ids'])} rules triggered.")

    c = chosen["capture"]["median"]
    verdict = "GO" if c >= thresholds["go_capture"] else "CONDITIONAL" if c >= thresholds["conditional_capture"] else "NO-GO"
    # Caveats about the data hold for every plan; the last two depend on which plan (route) is chosen.
    data_caveats = [r for r, on in [
        ("unresolved ingredients need review", bool(bom["unmapped_ingredients"])),
        ("some ingredients matched only approximately", bool(bom["approximate_ingredients"])),
        ("possible allergens flagged for review", bool(bom["allergen_review_flags"])),
        ("process steps outside the known vocabulary", bool(bom["unknown_process_steps"])),
        ("half-life reference is not fully computed", sig["reference_status"] != "computed"),
        ("market interest read from shared English, not the market's own language", sig["basis"] != "own_language"),
        ("product spec is a mock fallback", spec_source == "mock_fallback"),
    ] if on]

    def route_caveats(route: str) -> list[str]:
        return [r for r, on in [
            ("time to shelf comes from sample plant data, not real data", route != "observed_benchmark" and not own_plant),
            ("time to market from launches found with Google Search, not hand-checked",
             route == "observed_benchmark" and bench_source == "ai_discovered"),
        ] if on]
    downgrade = data_caveats + route_caveats(chosen["route"])
    if verdict == "GO" and (hard or downgrade):
        verdict = "CONDITIONAL"
    go_days = deadline["go_by_days"]["median"] if deadline["go_by_days"] else None
    decision = {"verdict": verdict, "market": market, "route": chosen["route"], "line": chosen["line"],
                "capture_median": c, "hard_failures": hard, "downgrade_reasons": downgrade,
                "deadline_go_days": go_days,
                "deadline_go_date": deadline["go_by_date"]["median"] if deadline["go_by_date"] else None,
                "days_to_shelf": chosen["total_days"],
                "time_to_shelf_basis": basis,
                "slack_days": (go_days - chosen["total_days"]) if go_days is not None else None,
                "faster_line_blocked": ({"line": blocked_route["line"], "total_days": blocked_route["total_days"],
                                         "blocked_by": blocked_route["blocked_by"]} if blocked_route else None)}
    ev_decision = reg.record("decision_rules", "timeline.yaml thresholds", decision)

    # What-if simulator: every plan the company could pick, timed here by the same task graph. The browser only
    # re-applies capture = 0.5 ** (T / h) and the thresholds above after the user shifts days or fad speed.
    def option(oid: str, label: str, r: dict, line: dict | None) -> dict:
        conflicts = sorted({f"{x['sku_name']}: {x['claim_label']}" for x in (line or {}).get("free_from_conflicts", [])})
        return {"id": oid, "label": label, "route": r["route"], "line": r["line"], "total_days": r["total_days"],
                "gaps": [g["step"] for g in (line or {}).get("gaps", [])],
                "gap_days": (line or {}).get("gap_lead_days", 0),
                "new_allergens": (line or {}).get("new_allergens_on_line", []),
                "conflicts": conflicts, "hard": bool(conflicts) and not sample_plant,
                "caveats": data_caveats + route_caveats(r["route"])}
    options = [option("co_manufacturer", "Co-manufacturer", co_man, None)]
    options += [option(f"in_house:{a['line_id']}", f"In-house · {a.get('line_name') or a['line_id']}",
                       route_eval("in_house", a), a) for a in ranked if a["feasible"]]
    if bench_route:
        options.append(option("observed_benchmark", "Real companies' observed pace", bench_route, None))
    chosen_id = f"in_house:{chosen['line']}" if chosen["route"] == "in_house" else chosen["route"]
    simulator = {"options": options, "chosen": chosen_id, "half_life_days": hl, "sustained": sustained,
                 "go_capture": thresholds["go_capture"], "conditional_capture": thresholds["conditional_capture"],
                 "official_hard_failures": hard}
    log("Decision", f"{verdict} via {chosen['route']} (capture {c}).")

    # 7. Market scan ---------------------------------------------------------
    scan_rows = []
    if scan:
        weights = catalog.scoring()["weights"]
        for mid, mk in mcfg["markets"].items():
            s = signals.market_signal(store, langs_by_market[mid], reference)
            cm = compliance_stage.evaluate(mid, mcfg, bom["product_allergens"], bom["product_lineage"], product_text,
                                           None, launch, plant.get("base_label_languages", []), catalog.place_names())
            scan_rows.append({
                "market": mid, "name": mk["name"],
                "signal": {k: s.get(k) for k in ("stage", "momentum_90d", "current_views_smoothed", "basis",
                                                 "languages_used", "peak_date")},
                "indicators": signals.aggregate_indicators(mk, indicators),
                "mandatory_declarations": [d["label"] for d in cm["declarations"]],
                "label_languages_needed": cm["label_languages_needed"],
                "place_name_rule": any(r["trigger_type"] == "place_name_in_product_name" for r in cm["triggered_rules"]),
                "compliance_burden": cm["n_mandatory_declarations"] + 2 * len(cm["label_languages_needed"]),
                "deadline_go_days": (None if s.get("stage") in ("sustained", "insufficient_data")
                                     else trend_stage.days_until_share(cfg["decision"]["go_capture"],
                                                                       s["reference_half_life_days"]["median"])),
                "allergen_source_status": mk["allergen_source"]["status"],
            })
        scan_rows = signals.score_markets(scan_rows, weights)
        reg.record("market_scan", "all markets", {"ranking": [{"market": r["market"], "score": r["score"]} for r in scan_rows],
                                                   "weights": weights})
        log("Market Scan", "Top markets: " + ", ".join(f"{r['market']} {r['score']}" for r in scan_rows[:3]))

    # 7b. Record the real-world evidence -----------------------------------------
    ev_refs = None
    if ref_rows:
        ev_refs = reg.record("reference_launches", ref_key, {
            "launches": ref_rows, "windows": ref_windows, "time_to_market": bench, "model_check": mcheck,
            "origin": {k: str(v) for k, v in ((ref_entry or {}).get("origin") or {}).items()}})

    # 8. Writer + verifier loop ------------------------------------------------
    state = {"trend": trend, "sku": sku_id, "as_of": as_of.isoformat(), "market": market,
             "market_name": m["name"],
             "evidence_index": {"trend": ev_trend, "spec": ev_spec, "bom": ev_bom, "lines": ev_lines,
                                "compliance": ev_comp, "decision": ev_decision, "deadline": ev_deadline,
                                **({"reference_launches": ev_refs} if ev_refs else {}),
                                **{f"timeline_{k}": v for k, v in route_ids.items()}},
             "decision": decision,
             "top_markets": [r["market"] for r in scan_rows[:3]]}
    attempts, feedback, plan, ver = [], None, None, None
    for attempt in range(1, 0 if quick else settings.max_writer_attempts + 1):
        plan = llm.structured("write_plan", prompts.write_plan(state, reg.for_prompt(), feedback), Plan,
                              mock_context={"attempt": attempt, "reg": reg, "state": state, "plant": plant})
        ver = verify(plan, reg, plant, comp, bom)
        rejected = [{"text": x["text"], "reason": x["reason"]} for x in ver["claims"] if x["status"] == "failed"]
        rejected += [{"text": o["text"], "reason": o["reason"]} for o in ver["orphans"]]
        rejected += [{"text": h, "reason": "hard failure"} for h in ver["hard_failures"]
                     if not any(h.startswith(r["text"]) for r in rejected)]
        attempts.append({"attempt": attempt, "trust": ver["trust"], "passed": ver["passed"], "rejected": rejected,
                         "failed": sum(1 for x in ver["claims"] if x["status"] == "failed") + len(ver["orphans"]),
                         "hard_failures": len(ver["hard_failures"])})
        log("Verifier", f"Attempt {attempt}: trust {ver['trust']}, passed={ver['passed']}.", "llm")
        if ver["passed"]:
            break
        feedback = feedback_for_writer(ver)

    mode = "offline" if settings.offline else ("mock_llm" if settings.mock_llm else "live")
    return {
        "mode": mode,
        "as_of": as_of.isoformat(),
        "quick": quick,
        "trend_input": trend,
        "evidence_index": state["evidence_index"],
        "trend": sig | {"article": title, "wikidata": qid, "source": trend_source,
                        "editions_available": sorted(titles)},
        "spec": spec.model_dump(),
        "bom": bom,
        "lines": [{k: v for k, v in a.items() if k != "skus_on_line"} for a in ranked],
        "allergen_blast_radius": blast,
        "compliance": comp,
        "routes": routes,
        "simulator": simulator,
        "shelf_deadline": deadline,
        "reference_launches": ref_rows,
        "reference_windows": ref_windows,
        "time_to_market_benchmark": (bench | {"source": bench_source}) if bench else None,
        "launch_discovery": ({k: v for k, v in discovered_info.items() if k != "launches"} if discovered_info else None),
        "model_check": mcheck,
        "trend_origin": {k: str(v) for k, v in (ref_entry.get("origin") or {}).items()} if ref_entry and ref_entry.get("origin") else None,
        "decision": decision,
        "market_scan": scan_rows,
        "plan": plan.model_dump() if plan else None,
        "verification": ver,
        "attempts": attempts,
        "evidence": reg.items,
        "provenance": provenance(settings, plant, spec_source, trend_source),
        "steps": steps,
        "llm_calls": llm.calls,
        "cache_hits": llm.cache_hits,
        "http_requests": http.requests if http else 0,
    }
