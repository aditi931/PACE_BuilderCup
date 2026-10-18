import datetime as dt
import io
import json
from pathlib import Path
import os

os.environ["MOCK_LLM"] = "1"
os.environ["OFFLINE"] = "1"
os.environ["CACHE_DIR"] = "/tmp/t2l_test_cache"

import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.evidence import EvidenceRegistry  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Claim, Plan, ProductSpec  # noqa: E402
from app.stages import bom, catalog, compliance, feasibility, signals, timeline, trend  # noqa: E402
from app.stages.ingredients import taxonomy  # noqa: E402
from app.stages.verifier import verify  # noqa: E402

DUBAI = ProductSpec.model_validate(catalog.demo_trends()[0]["spec"])
PLANT = catalog.default_plant()


def _product(spec=DUBAI):
    res = bom.resolve_spec(spec)
    return res, sorted({a for r in res for a in r["allergens"]}), sorted({n for r in res for n in r["lineage"]})


# --- ingredient taxonomy (Open Food Facts) ---------------------------------
def test_taxonomy_follows_parents_to_allergens():
    tx = taxonomy()
    r = tx.resolve("pistachio paste")
    assert r["match"] == "exact" and "en:nuts" in r["allergens"] and "en:pistachio-nuts" in r["lineage"]
    assert tx.resolve("tahini")["allergens"] == ["en:sesame-seeds"]
    assert "en:milk" in tx.resolve("ghee")["allergens"]


def test_taxonomy_overrides_components_and_review_flags():
    tx = taxonomy()
    assert tx.resolve("kataifi")["match"] == "curated" and "en:gluten" in tx.resolve("kataifi")["allergens"]
    assert tx.resolve("ube")["labels"] == ["yam"]                      # not the Igbo word for pear
    comp = tx.resolve("mystery crunch", ["wheat flour"])
    assert comp["match"] == "components" and comp["allergens_from_components"] == ["en:gluten"]
    assert "en:nuts" in tx.resolve("hazelnut spread")["review_flags"]  # never silently dropped
    part = tx.resolve("pistachio cream")
    assert part["match"] == "partial" and {"en:nuts", "en:milk"} <= set(part["allergens"])


# --- compliance per market --------------------------------------------------
def _comp(market, launch=dt.date(2025, 6, 1), text="Dubai chocolate bar"):
    _, a, ln = _product()
    return compliance.evaluate(market, catalog.markets(), a, ln, text, None, launch, ["en", "hi"], catalog.place_names())


def test_markets_differ_for_the_same_product():
    assert "en:sesame-seeds" in [x["id"] for x in _comp("IN")["allergens_not_regulated_here"]]  # India: no sesame
    assert "sesame" in [d["id"] for d in _comp("EU")["declarations"]]
    assert "pistachio" in [d["id"] for d in _comp("AU")["declarations"]]                 # PEAL names each nut
    assert "en:nuts" in [x["id"] for x in _comp("KR")["allergens_not_regulated_here"]]   # no pistachio in KR list


def test_effective_dates_follow_launch_date():
    assert _comp("CN", dt.date(2026, 1, 1))["declarations"] == []
    assert _comp("CN", dt.date(2026, 1, 1))["upcoming_declarations"]
    assert _comp("CN", dt.date(2027, 4, 1))["declarations"]
    assert not _comp("JP", dt.date(2025, 6, 1))["recommended_declarations"] or \
        "pistachio" not in [d["id"] for d in _comp("JP", dt.date(2025, 6, 1))["recommended_declarations"]]
    assert "pistachio" in [d["id"] for d in _comp("JP", dt.date(2026, 6, 1))["recommended_declarations"]]


def test_place_names_and_label_languages():
    pl = catalog.place_names()
    assert [p["term"] for p in compliance.find_place_names("Dubai chocolate", pl)] == ["dubai"]
    assert compliance.find_place_names("Brazil nut orange bar", pl) == []
    gcc = _comp("GCC")
    assert {"GCC-ARABIC", "GCC-NOT-MISLEADING"} <= set(gcc["triggered_rule_ids"])
    assert gcc["label_languages_needed"] == ["ar"]


def test_every_market_rule_has_a_source_and_known_status():
    for mid, m in catalog.markets()["markets"].items():
        assert m["allergen_source"]["url"].startswith("http"), mid
        for r in m["rules"]:
            assert r["source"]["status"] in {"verified_official", "verified_secondary", "needs_review"}, r["rule_id"]


# --- plant, feasibility, timeline -------------------------------------------
def test_line_allergens_are_computed_from_skus():
    assert PLANT["lines"]["L3"]["allergens_present"] == ["en:milk", "en:nuts"]
    assert "en:gluten" in PLANT["lines"]["B1"]["allergens_present"]


def test_allergen_blast_radius_and_line_choice():
    _, a, ln = _product()
    res = {lid: feasibility.assess_line(lid, line, list(DUBAI.process_steps), set(a), set(ln), catalog.process_map(),
                                        catalog.equipment_catalog(), 14, catalog.free_from_claims())
           for lid, line in PLANT["lines"].items()}
    assert any(c["sku_id"] == "KIDS-NF-40" for c in res["L2"]["free_from_conflicts"])
    assert any(c["sku_id"] == "GF-BITES-25" for c in res["L4"]["free_from_conflicts"])
    assert feasibility.rank_lines(list(res.values()))[0]["line_id"] == "L3"


def test_oats_void_gluten_free_via_lineage():
    assert feasibility.claim_conflicts("gluten_free", set(), {"en:oat"}, catalog.free_from_claims()) == ["en:oat"]


def test_timeline_longest_path_and_localisation():
    cfg = catalog.timeline_cfg()
    assert timeline.schedule(timeline.build_route("co_manufacturer", cfg))["total_days"] == 55
    s = timeline.schedule(timeline.build_route("in_house", cfg, gap_days=21, needs_validation=True))
    assert s["total_days"] == 21 + 10 + 14 + 7 + 10 and s["critical_path"][0] == "gap_resolution"
    loc = timeline.build_route("co_manufacturer", {**cfg, "label_localisation": 30}, localisation=True)
    assert timeline.schedule(loc)["total_days"] == 14 + 10 + 30 + 7 + 10


def test_trend_stage_and_capture():
    start, peak = dt.date(2022, 1, 1), dt.date(2023, 6, 1)
    s = trend.synthetic_series(start, dt.date(2024, 1, 1), peak, rise_days=10, half_life=20)
    rep = trend.analyze(s, dt.date(2023, 8, 1), catalog.fad_reference())
    assert rep["stage"] == "decaying" and rep["reference_class"] == "flash"
    assert trend.capture_share(120, 120) == 0.5 and trend.capture_share(90, 30) == 0.125
    assert trend.combine([[(start, 1)], [(start, 2)]]) == [(start, 3)]


def test_market_languages_and_scoring():
    mk, ind = catalog.markets(), catalog.market_indicators()
    assert signals.market_languages(mk["markets"]["JP"], ind, {}) == ["ja"]
    assert signals.market_languages({"countries": ["SG"]}, ind, {"cmn": "zh"})[0] == "zh"
    agg = signals.aggregate_indicators(mk["markets"]["GCC"], ind)
    assert agg["population"] > 50_000_000 and agg["gdp_per_capita_usd"] > 10_000
    rows = [{"signal": {"momentum_90d": m, "current_views_smoothed": 10, "basis": b},
             "indicators": {"gdp_usd": 1, "gdp_per_capita_usd": 1}, "compliance_burden": 1}
            for m, b in [(2.0, "own_language"), (1.0, "shared_english")]]
    ranked = signals.score_markets(rows, {"interest_momentum": 1, "interest_level": 1})
    assert ranked[0]["signal"]["momentum_90d"] == 2.0
    assert ranked[1]["score_components"]["interest_level"] == 0.0   # shared English level is not scored


# --- verifier ---------------------------------------------------------------
def _setup_reg():
    reg = EvidenceRegistry()
    eid = reg.record("timeline", "x", {"total_days": 62, "launch_date": "2025-05-02", "capture": {"median": 0.7}})
    comp = {"triggered_rule_ids": ["IN-ALLERGEN-DECL"], "unverified_rule_ids": []}
    b = {"new_allergens_vs_our_sku": ["en:nuts"]}
    return reg, eid, comp, b


def test_verifier_catches_wrong_number_orphan_and_process():
    reg, eid, comp, b = _setup_reg()
    plan = Plan(headline="GO", rationale="Prices rose 60% and capture is 70%.", actions=[], claims=[
        Claim(text="capture 70%", kind="numeric", evidence_ids=[eid], field="capture.median", value=70),
        Claim(text="62 days", kind="numeric", evidence_ids=[eid], field="total_days", value=55),
        Claim(text="L2 deposits", kind="process", line="L2", capability="one_shot_depositing", capability_present=True),
        Claim(text="launch", kind="date", evidence_ids=[eid], field="launch_date", date_value="2025-05-02"),
    ])
    v = verify(plan, reg, PLANT, comp, b)
    st = {c["text"]: c["status"] for c in v["claims"]}
    assert st == {"capture 70%": "verified", "62 days": "failed", "L2 deposits": "failed", "launch": "verified"}
    assert [o["text"] for o in v["orphans"]] == ["60%"]
    assert v["hard_failures"] and not v["passed"]


def test_verifier_blocks_no_new_allergens_claim():
    reg, eid, comp, b = _setup_reg()
    plan = Plan(headline="GO", rationale="There are no new allergens.", actions=[], claims=[])
    assert verify(plan, reg, PLANT, comp, b)["hard_failures"]


# --- settings and API -------------------------------------------------------
def test_auth_detection_for_express_keys():
    base = get_settings()
    def mk(key, auth="auto"):
        return Settings(**{**base.__dict__, "google_api_key": key, "gemini_auth": auth})
    assert mk("AQ.abc").resolved_auth == "vertex_express"
    assert mk("AIzaXYZ").resolved_auth == "ai_studio"
    assert mk("").resolved_auth == "vertex_adc"
    assert mk("AQ.abc", "ai_studio").resolved_auth == "ai_studio"


@pytest.mark.parametrize("demo", catalog.demo_trends(), ids=lambda d: d["trend"])
def test_end_to_end_every_demo_trend(demo):
    c = TestClient(app)
    r = c.post("/api/run", data={"trend": demo["trend"], "sku_id": "CLASSIC-100",
                                 "as_of": str(demo["suggested_as_of"]), "market": demo["suggested_market"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["mode"] == "offline" and j["compliance"]["market"] == demo["suggested_market"]
    assert len(j["market_scan"]) == len(catalog.markets()["markets"])
    assert j["attempts"][-1]["passed"] is True
    assert j["decision"]["verdict"] in {"CONDITIONAL", "NO-GO"}   # placeholder reference caps GO
    assert any(p["status"] == "real_open_data" for p in j["provenance"])


def test_dubai_end_to_end_details():
    c = TestClient(app)
    j = c.post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "CLASSIC-100",
                                 "as_of": "2025-03-01", "market": "GCC"}).json()
    assert j["decision"]["line"] == "L3"
    assert j["attempts"][0]["passed"] is False      # mock writer errs on purpose; verifier catches it
    assert "GCC-NOT-MISLEADING" in j["compliance"]["triggered_rule_ids"]
    d = c.post("/api/run", data={"trend": "Dalgona coffee", "sku_id": "COLDCOFFEE-200",
                                 "as_of": "2020-04-05", "market": "IN", "scan": "false"}).json()
    assert d["market_scan"] == [] and d["decision"]["market"] == "IN"


def test_plant_upload_validation():
    c = TestClient(app)
    good = yaml.safe_dump({"company": "X", "lines": {"A": {"capabilities": ["oven"]}},
                           "skus": {"S": {"name": "Bun", "line": "A", "format": "bakery", "ingredients": ["wheat flour"],
                                          "process_steps": ["baking"]}}})
    r = c.post("/api/plant/validate", files={"plant_file": ("p.yaml", io.BytesIO(good.encode()), "text/yaml")})
    assert r.status_code == 200 and r.json()["lines"]["A"]["allergens_present"] == ["en:gluten"]
    bad = c.post("/api/plant/validate", files={"plant_file": ("p.yaml", io.BytesIO(b"lines: {}"), "text/yaml")})
    assert bad.status_code == 400
    run = c.post("/api/run", data={"trend": "Croffle", "sku_id": "S", "as_of": "2021-03-01", "market": "KR"},
                 files={"plant_file": ("p.yaml", io.BytesIO(good.encode()), "text/yaml")})
    assert run.status_code == 200, run.text


def test_config_lists_markets_and_demos():
    j = TestClient(app).get("/api/config").json()
    assert len(j["markets"]) == 12 and len(j["demo_trends"]) >= 7 and j["mode"] == "offline"


# --- live code path against a fake Wikimedia (same JSON shapes as the real APIs) ------------
class FakeHttp:
    requests = 0

    def __init__(self, *a, **k):
        pass

    def get(self, url, params=None, allow_404=False):
        params = params or {}
        if params.get("action") == "opensearch":
            return ["q", ["Dubai chocolate", "Dubai Chocolate (brand)"], [], []]
        if params.get("prop") == "pageprops":
            return {"query": {"pages": {"1": {"pageprops": {"wikibase_item": "Q123"}}}}}
        if params.get("action") == "wbgetentities":
            return {"entities": {"Q123": {"sitelinks": {
                "enwiki": {"title": "Dubai chocolate"}, "arwiki": {"title": "شوكولاتة دبي"},
                "jawiki": {"title": "ドバイチョコレート"}, "commonswiki": {"title": "x"}}}}}
        if "pageviews" in url:
            lang = url.split("/per-article/")[1].split(".")[0]
            s = trend.offline_series("Dubai chocolate", lang, dt.date(2025, 3, 1))
            return {"items": [{"timestamp": d.strftime("%Y%m%d00"), "views": v} for d, v in s]}
        raise AssertionError(url)


def test_live_path_with_fake_wikimedia(monkeypatch):
    from app import pipeline
    from app.llm import LLM
    from app.mock import MOCK_HANDLERS
    monkeypatch.setattr(trend, "Http", FakeHttp)
    s = Settings(**{**get_settings().__dict__, "offline": False})
    r = pipeline.run("Dubai chocolate", "CLASSIC-100", dt.date(2025, 3, 1), "GCC", None, LLM(s, MOCK_HANDLERS), s)
    assert r["trend"]["wikidata"] == "Q123"
    assert r["trend"]["languages_used"] == ["ar"] and r["trend"]["basis"] == "own_language"
    assert set(r["trend"]["editions_available"]) == {"en", "ar", "ja"}
    jp = next(x for x in r["market_scan"] if x["market"] == "JP")
    br = next(x for x in r["market_scan"] if x["market"] == "BR")
    assert jp["signal"]["languages_used"] == ["ja"]
    assert br["signal"]["basis"] == "shared_english_fallback" or br["signal"]["languages_used"] == ["en"]


def test_faster_line_that_voids_a_claim_is_shown_not_chosen():
    j = TestClient(app).post("/api/run", data={"trend": "Croffle", "sku_id": "CROISSANT-60", "as_of": "2021-03-01",
                                               "market": "KR", "scan": "false"}).json()
    blocked = j["decision"]["faster_line_blocked"]
    assert blocked["line"] == "B1" and "Eggless Rusk 200 g: Egg-free" in blocked["blocked_by"]
    assert j["decision"]["line"] != "B1"


# --- shelf deadline (public data only) --------------------------------------
def test_shelf_deadline_inverts_the_capture_model():
    assert trend.days_until_share(0.5, 120) == 120          # half the interest left after one half-life
    assert trend.days_until_share(0.25, 30) == 60           # a quarter after two
    for h in (17, 45, 133):                                  # deadline is never late: capture there >= share
        assert trend.capture_share(trend.days_until_share(0.5, h), h) >= 0.5
    d = trend.shelf_deadline({"p25": 10, "median": 30, "p75": 60}, dt.date(2025, 1, 1), 0.5, 0.25, False)
    assert d["go_by_days"] == {"p25": 10, "median": 30, "p75": 60}
    assert d["go_by_date"]["median"] == "2025-01-31" and d["conditional_by_days"]["median"] == 60
    s = trend.shelf_deadline({"median": 30}, dt.date(2025, 1, 1), 0.5, 0.25, True)
    assert s["go_by_days"] is None and s["sustained"] is True
    with pytest.raises(ValueError):
        trend.days_until_share(1.0, 30)


def test_deadline_in_run_output_and_verified_by_writer():
    j = TestClient(app).post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "CLASSIC-100",
                                               "as_of": "2025-03-01", "market": "GCC"}).json()
    d, dl = j["decision"], j["shelf_deadline"]
    assert d["deadline_go_days"] == dl["go_by_days"]["median"]
    assert d["slack_days"] == d["deadline_go_days"] - d["days_to_shelf"]
    claims = {c["field"]: c["status"] for c in j["verification"]["claims"] if c.get("field")}
    assert claims.get("go_by_days.median") == "verified" and claims.get("go_by_date.median") == "verified"
    assert all("deadline_go_days" in r for r in j["market_scan"])


# --- local setup helpers -------------------------------------------------------
def test_dotenv_loader_handles_quotes_comments_and_never_overrides(tmp_path, monkeypatch):
    from app.config import load_dotenv
    monkeypatch.setenv("T2L_KEEP", "real")
    f = tmp_path / ".env"
    f.write_text('# comment\nexport T2L_A="Trend2Line/0.2 (me@x.com)"\nT2L_B=plain  # note\nT2L_KEEP="from-file"\n',
                 encoding="utf-8")
    for k in ("T2L_A", "T2L_B"):
        monkeypatch.delenv(k, raising=False)
    loaded = load_dotenv(f)
    assert os.environ["T2L_A"] == "Trend2Line/0.2 (me@x.com)" and os.environ["T2L_B"] == "plain"
    assert os.environ["T2L_KEEP"] == "real" and "T2L_KEEP" not in loaded


def test_no_credentials_defaults_to_mock(monkeypatch):
    for k in ("MOCK_LLM", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    assert get_settings().mock_llm is True
    monkeypatch.setenv("GOOGLE_API_KEY", "AQ.x")
    assert get_settings().mock_llm is False


def test_shell_scripts_use_unix_line_endings():
    from app.config import BASE_DIR
    for f in ("deploy.sh", "Dockerfile", "scripts/check_no_anthropic.sh"):
        assert b"\r\n" not in (BASE_DIR / f).read_bytes(), f


# --- real reference launches (public data) ------------------------------------
from app.stages import references  # noqa: E402

LAUNCHES = catalog.reference_launches()["trends"]["dubai_chocolate"]["launches"]


def test_engine_reproduces_lindts_printed_allergen_declaration():
    """Validation against a real pack: Lindt Dubai Style declares pistachio, hazelnut, almond, wheat, barley,
    soybean and milk. The engine must find exactly those allergen groups from the printed ingredient list."""
    lindt = next(x for x in LAUNCHES if x["brand"] == "Lindt" and "pack" in x)
    tx = taxonomy()
    res = [tx.resolve(i) for i in lindt["pack"]["ingredients"]]
    assert all(r["match"] in ("exact", "variant", "partial") for r in res), [r["name"] for r in res]
    found = {a for r in res for a in r["allergens"]}
    assert found == {"en:milk", "en:gluten", "en:soybeans", "en:nuts"}
    lineage = sorted({n for r in res for n in r["lineage"]})
    eu = compliance.evaluate("EU", catalog.markets(), sorted(found), lineage, "Lindt Dubai Style Chocolate", None,
                             dt.date(2025, 8, 18), ["en"], catalog.place_names())
    assert {d["id"] for d in eu["declarations"]} == {"gluten_cereals", "soybeans", "milk", "nuts"}


def test_reference_launch_file_is_sourced():
    for key, entry in catalog.reference_launches()["trends"].items():
        assert entry["match"], key
        for ln in entry["launches"]:
            assert ln["source_url"].startswith("https://") and ln["date_basis"], ln["brand"]
            assert ln["market"] in catalog.markets()["markets"], ln["market"]


def test_reference_matching_windows_benchmark_and_model_check():
    cfg = catalog.reference_launches()
    assert references.match("Kunafa chocolate", cfg)[0] == "dubai_chocolate"
    assert references.match("Dubai Style Chocolate", cfg)[0] == "dubai_chocolate"
    assert references.match("Croffle", cfg) == (None, None)

    class Store:  # Hindi curve: peak 2025-01-01, rises over ~10 days, half-life 30 days
        def get(self, lang):
            if lang != "hi":
                return []
            return trend.synthetic_series(dt.date(2024, 1, 1), dt.date(2026, 10, 1), dt.date(2025, 1, 1),
                                          rise_days=10, half_life=30, baseline=0)

    def launch(brand, date):
        return {"company": brand, "brand": brand, "product": "p", "market": "IN", "date": date,
                "date_basis": "x", "source_title": "t", "source_url": "https://x"}
    entry = {"launches": [launch("Early", "2024-06-01"), launch("A", "2025-01-31"), launch("B", "2025-03-15"),
                          launch("C", "2025-06-01"), launch("Future", "2027-01-01")]}
    rows, windows = references.evaluate(entry, Store(), {"IN": ["en", "hi"]}, dt.date(2026, 10, 1))
    w = windows["IN"]
    assert w["languages_used"] == ["hi"] and w["observed_half_life_days"] and w["window_closed_date"]
    by = {r["brand"]: r for r in rows}
    assert by["Early"]["position"] == "before_takeoff"
    assert by["A"]["position"] == "after_peak_window_open" and 0.4 < by["A"]["interest_at_launch_share_of_peak"] < 0.65
    assert by["C"]["position"] == "after_window_closed"
    assert by["Future"]["status"] == "after_run_date"
    bench = references.benchmark(rows)
    assert bench["n"] == 3 and bench["fastest"] == "A" and bench["p25"] <= bench["median"] <= bench["p75"]
    check = references.model_check(windows, catalog.fad_reference())
    assert check[0]["market"] == "IN" and check[0]["observed"] == w["observed_half_life_days"]


def test_observed_benchmark_replaces_sample_durations_in_the_verdict(monkeypatch):
    """With real launches available and only sample plant data, the verdict uses real time to market."""
    from app import pipeline
    from app.llm import LLM
    from app.mock import MOCK_HANDLERS
    fake = {"n": 3, "p25": 60.0, "median": 90.0, "p75": 120.0, "fastest": "A", "fastest_days": 50,
            "companies": ["A", "B", "C"], "definition": "x"}
    monkeypatch.setattr(references, "benchmark", lambda rows, min_launches=2: fake)
    s = get_settings()
    r = pipeline.run("Dubai chocolate", "CLASSIC-100", dt.date(2026, 10, 1), "IN", None, LLM(s, MOCK_HANDLERS), s,
                     scan=False)
    d = r["decision"]
    assert d["route"] == "observed_benchmark" and d["days_to_shelf"] == 90
    assert d["time_to_shelf_basis"].startswith("observed")
    assert "time to shelf comes from sample plant data, not real data" not in d["downgrade_reasons"]
    assert r["attempts"][-1]["passed"] is True


def test_reference_launches_reach_the_api():
    j = TestClient(app).post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "CLASSIC-100",
                                               "as_of": "2026-10-01", "market": "IN", "scan": "false"}).json()
    brands = [r["brand"] for r in j["reference_launches"]]
    assert {"Läderach", "Fabelle", "Cadbury Dairy Milk Silk", "Lindt"} <= set(brands)
    assert any(p["input"] == "Real reference launches" for p in j["provenance"])


# --- company profile from public Open Food Facts data -------------------------------------
def test_company_profile_builder_from_off_shaped_data():
    from scripts.build_company_profile import build_profile
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "data" / "category_lines.yaml").read_text())
    products = [  # same field shapes as the Open Food Facts v2 search API
        {"code": "111", "product_name": "Dubai Style Milk", "categories_tags": ["en:snacks", "en:chocolates"],
         "ingredients_tags": ["en:milk-chocolate", "en:pistachio-paste", "en:wheat-flour", "en:not-a-real-tag"],
         "allergens_tags": ["en:gluten", "en:milk", "en:nuts"], "traces_tags": ["en:peanuts", "en:sesame-seeds"],
         "labels_tags": [], "manufacturing_places": "Aachen"},
        {"code": "222", "product_name": "Dark 70%", "categories_tags": ["en:chocolates"],
         "ingredients_tags": ["en:cocoa-mass", "en:sugar", "en:cocoa-butter"], "allergens_tags": [],
         "traces_tags": ["en:milk"], "labels_tags": ["en:gluten-free", "en:vegan"]},
        {"code": "333", "product_name": "Mystery", "categories_tags": ["en:chocolates"], "ingredients_tags": []},
        {"code": "444", "product_name": "Pet food", "categories_tags": ["en:pet-food"],
         "ingredients_tags": ["en:sugar"]},
    ]
    prof = build_profile(products, "demo", "EU", ["en"], cfg)
    assert prof["data_quality"] == {"products_fetched": 4, "products_used": 2, "no_ingredients": 1,
                                    "no_category_match": 1}
    assert list(prof["lines"]) == ["CHOC"] and "(inferred)" in prof["lines"]["CHOC"]["name"]
    assert {"en:peanuts", "en:sesame-seeds", "en:milk"} <= set(prof["lines"]["CHOC"]["extra_allergens"])
    assert prof["skus"]["222"]["free_from"] == ["dairy_free", "egg_free", "gluten_free"]
    plant = catalog.prepare_plant(prof)          # loads like any plant file
    assert "en:sesame-seeds" in plant["lines"]["CHOC"]["allergens_present"]
    # the trace "may contain milk" on the line already voids the dark bar's dairy-free claim for nothing new;
    # a new Dubai-style product with gluten would void its gluten-free claim
    j = TestClient(app).post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "111", "as_of": "2025-03-01",
                                               "market": "EU", "scan": "false"},
                             files={"plant_file": ("p.yaml", io.BytesIO(yaml.safe_dump(prof).encode()), "text/yaml")})
    assert j.status_code == 200, j.text
    conflicts = {(c["sku_id"], c["claim"]) for c in j.json()["allergen_blast_radius"]}
    assert ("222", "gluten_free") in conflicts
    prov = {p["input"]: p["status"] for p in j.json()["provenance"]}
    assert prov["Plant, lines and SKUs"] == "public_pack_data_inferred_lines"


def test_company_selector_and_case_study_report(tmp_path):
    from app.config import DATA_DIR
    from scripts.build_company_profile import build_profile
    cfg = yaml.safe_load((DATA_DIR / "category_lines.yaml").read_text())
    prof = build_profile([{"code": "9", "product_name": "Nut-free Bar", "categories_tags": ["en:chocolates"],
                           "ingredients_tags": ["en:milk-chocolate", "en:rice"], "allergens_tags": ["en:milk"],
                           "traces_tags": [], "labels_tags": ["en:nut-free"]}], "testco", "IN", ["en"], cfg)
    folder = DATA_DIR / "companies"
    folder.mkdir(exist_ok=True)
    f = folder / "zz_testco.yaml"
    f.write_text(yaml.safe_dump(prof), encoding="utf-8")
    catalog.companies.cache_clear()
    try:
        c = TestClient(app)
        cos = c.get("/api/config").json()["companies"]
        assert "zz_testco" in cos and cos["zz_testco"]["products"] == 1
        j = c.post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "9", "as_of": "2026-10-01",
                                     "market": "IN", "company": "zz_testco"}).json()
        assert any(x["sku_id"] == "9" and x["claim"] == "nut_free" for x in j["allergen_blast_radius"])
        assert c.post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "9", "as_of": "2026-10-01",
                                        "market": "IN", "company": "nope"}).status_code == 400
        import argparse
        from scripts.case_study import render
        md = render(j, argparse.Namespace(trend="Dubai chocolate", market="IN", company="zz_testco", as_of="2026-10-01"))
        for heading in ("Where the trend came from", "Real launches", "What the label must say",
                        "Validation against a real pack", "Allergen blast radius", "Data sources"):
            assert heading in md, heading
        assert "OFFLINE MODE" in md
    finally:
        f.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
        catalog.companies.cache_clear()


# --- Google Cloud services (fakes shaped like the real client responses) --------------------
from types import SimpleNamespace as NS  # noqa: E402

from app.stages import discovery, radar  # noqa: E402
from app import storage  # noqa: E402


def _live_settings(tmp_path, **kw):
    base = get_settings()
    return Settings(**{**base.__dict__, "mock_llm": False, "offline": False, "gemini_model": "test-flash",
                       "google_api_key": "", "gcp_project": "demo-proj", "gemini_auth": "auto",
                       "cache_dir": tmp_path, **kw})


def test_grounded_call_reads_google_search_sources(tmp_path):
    from app.llm import LLM
    s = _live_settings(tmp_path)
    assert s.resolved_auth == "vertex_adc"                 # project + no key -> Vertex AI with service account
    web = NS(uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc", title="foodtechbiz.com",
             domain="foodtechbiz.com")
    resp = NS(text='[{"brand": "X"}]', candidates=[NS(grounding_metadata=NS(grounding_chunks=[NS(web=web), NS(web=web)]))])
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(config)
            return resp

        def list(self, config=None):
            calls.append(config)
            return [NS(name="publishers/google/models/gemini-x-flash"), NS(name="publishers/google/models/imagen")]
    llm = LLM(s)
    llm._client = NS(models=Models())
    text, sources = llm.grounded("t", "find launches")
    assert text == '[{"brand": "X"}]' and sources == [{"title": "foodtechbiz.com", "domain": "foodtechbiz.com",
                                                       "uri": web.uri}]
    assert calls[0].tools[0].google_search is not None     # the Google Search tool was really requested
    assert llm.grounded("t", "find launches")[1] == sources and llm.cache_hits == 1
    assert llm.list_models() == ["gemini-x-flash"] and calls[-1] == {"query_base": True}


def test_discovered_launches_are_validated_by_code():
    mk = catalog.markets()["markets"]
    rows = [
        {"company": "Nestlé India", "brand": "KitKat", "product": "Kunafa", "country": "IN", "date": "2025-09-10",
         "date_basis": "press release", "source_url": "https://example.com/a"},
        {"company": "Future Co", "brand": "F", "country": "IN", "date": "2031-01-01"},
        {"company": "Nowhere", "brand": "N", "country": "AQ", "date": "2025-01-01"},
        {"company": "Month Co", "brand": "M", "country": "DE", "date": "2025-04"},
        {"company": "Mondelez India", "brand": "Cadbury Dairy Milk Silk", "country": "IN", "date": "2026-06-18"},
        {"company": "", "brand": "", "country": "IN", "date": "2025-01-01"},
    ]
    ok, bad = discovery.validate(rows, [{"uri": "https://x"}], mk, dt.date(2026, 10, 1), LAUNCHES)
    assert [(r["brand"], r["market"], r["date"].isoformat()) for r in ok] == [("KitKat", "IN", "2025-09-10"),
                                                                             ("M", "EU", "2025-04-01")]
    assert "month only" in ok[1]["date_basis"] and all(r["verification"] == "ai_discovered" for r in ok)
    assert sorted(b["reason"] for b in bad) == ["already in the curated list", "country not covered",
                                               "date in the future", "no company or brand"]
    none, rej = discovery.validate(rows[:1], [], mk, dt.date(2026, 10, 1), [])
    assert none == [] and rej[0]["reason"] == "no web sources returned by Google Search"
    assert discovery._parse_json_array('Here you go:\n```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert discovery._parse_json_array("no json") == []


def test_live_pipeline_uses_discovered_launches_for_any_trend(monkeypatch, tmp_path):
    """Croffle has no curated launches; Gemini + Google Search finds them, code places them on the curve."""
    from app import pipeline
    from app.llm import LLM
    from app.mock import MOCK_HANDLERS
    monkeypatch.setattr(trend, "Http", FakeHttp)

    def found(prompt, ctx):
        return {"text": json.dumps([{"company": "A Cafe Group", "brand": "A", "product": "croffle", "country": "AE",
                                     "date": "2025-01-20", "date_basis": "press release", "source_url": "https://a.example"},
                                    {"company": "B Foods", "brand": "B", "product": "croffle", "country": "AE",
                                     "date": "2025-02-10", "date_basis": "launch", "source_url": "https://b.example"}]),
                "sources": [{"title": "a.example", "domain": "a.example", "uri": "https://a.example"}]}
    handlers = {**MOCK_HANDLERS, "discover_launches": found}
    s = Settings(**{**get_settings().__dict__, "offline": False, "mock_llm": True, "grounding": True})
    r = pipeline.run("Dubai chocolate", "CLASSIC-100", dt.date(2025, 3, 1), "GCC", None, LLM(s, handlers), s, scan=False)
    ai = [x for x in r["reference_launches"] if x["verification"] == "ai_discovered"]
    assert {x["brand"] for x in ai} == {"A", "B"} and all(x["status"] == "ok" for x in ai)
    assert r["launch_discovery"]["grounding_sources"][0]["uri"] == "https://a.example"
    assert any(p["input"] == "Launches found live" and p["status"] == "ai_discovered" for p in r["provenance"])


def test_bigquery_radar_query_is_capped_and_parameterised():
    captured = {}

    class Job:
        def result(self):
            return [NS(items=lambda: {"country_code": "IN", "term": "kunafa chocolate", "gain": 4500, "best_rank": 2,
                                      "last_seen": "2026-10-10"}.items())]

    class Client:
        def query(self, sql, job_config):
            captured["sql"], captured["cfg"] = sql, job_config
            return Job()
    rows = radar.query("p", ["IN", "US", "JP"], 14, 5, client=Client())
    assert rows[0]["term"] == "kunafa chocolate"
    assert captured["cfg"].maximum_bytes_billed == 5 * 1024 ** 3
    params = {p.name: p for p in captured["cfg"].query_parameters}
    assert params["codes"].values == ["IN", "JP"] and params["include_us"].value is True
    assert "international_top_rising_terms" in captured["sql"] and "@days" in captured["sql"]


def test_radar_keeps_googles_numbers_and_gemini_only_labels():
    from app.llm import LLM
    from app.mock import MOCK_HANDLERS
    rows = [{"country_code": "IN", "term": "kunafa chocolate", "gain": 4500, "best_rank": 2, "last_seen": "2026-10-10"},
            {"country_code": "IN", "term": "cricket score", "gain": 9000, "best_rank": 1, "last_seen": "2026-10-10"},
            {"country_code": "JP", "term": "mochi donut", "gain": 300, "best_rank": 7, "last_seen": "2026-10-09"},
            {"country_code": "AE", "term": "boba tea", "gain": 1200, "best_rank": 4, "last_seen": "2026-10-08"}]
    s = Settings(**{**get_settings().__dict__, "mock_llm": True})
    out = radar.build(rows, catalog.markets()["markets"], LLM(s, MOCK_HANDLERS))
    assert [(i["term"], i["market"], i["percent_gain"]) for i in out["food_trends"]] == [
        ("kunafa chocolate", "IN", 4500), ("boba tea", "GCC", 1200), ("mochi donut", "JP", 300)]
    assert out["terms_scanned"] == 4


def test_run_store_local_and_firestore(tmp_path):
    res = {"decision": {"market": "IN", "verdict": "GO", "deadline_go_date": "2026-11-01", "days_to_shelf": 40},
           "trend": {"article": "Dubai chocolate"}, "mode": "live", "big": list(range(1000))}
    loc = storage.LocalStore(tmp_path / "runs")
    rid = loc.save(res)
    assert loc.get(rid)["big"][-1] == 999 and loc.list()[0]["verdict"] == "GO" and loc.get("../etc") is None

    docs = {}

    class Doc:
        def __init__(self, k):
            self.k = k

        def set(self, d):
            docs[self.k] = d

        def get(self):
            return NS(exists=self.k in docs, to_dict=lambda: docs[self.k])

    class Query:
        def order_by(self, *a, **k):
            return self

        def limit(self, n):
            return self

        def stream(self):
            return [NS(to_dict=lambda d=d: d) for d in docs.values()]

    class Coll(Query):
        def document(self, k):
            return Doc(k)
    fs = storage.FirestoreStore("p", client=NS(collection=lambda name: Coll()))
    rid = fs.save(res)
    assert isinstance(docs[rid]["result_gz"], bytes) and len(docs[rid]["result_gz"]) < 1_000_000
    assert fs.get(rid)["trend"]["article"] == "Dubai chocolate"
    assert "result_gz" not in fs.list()[0] and fs.list()[0]["market"] == "IN"


def test_run_history_and_radar_endpoints():
    c = TestClient(app)
    j = c.post("/api/run", data={"trend": "Dubai chocolate", "sku_id": "CLASSIC-100", "as_of": "2025-03-01",
                                 "market": "GCC", "scan": "false"}).json()
    assert j["run_id"] and j["run_store"] == "local" and j["services"]["gemini"]["live"] is False
    assert c.get(f"/api/runs/{j['run_id']}").json()["decision"]["verdict"] == j["decision"]["verdict"]
    assert any(r["id"] == j["run_id"] for r in c.get("/api/runs").json()["runs"])
    assert c.get("/api/runs/doesnotexist").status_code == 404
    assert c.get("/api/radar").status_code == 400          # offline in tests: BigQuery not available
    cfg = c.get("/api/config").json()["services"]
    assert set(cfg) == {"gemini", "grounding", "bigquery", "run_store", "project"}


def test_run_store_auto_selection():
    from app.config import _run_store
    assert _run_store("auto", "proj", "trend2line") == "firestore"   # on Cloud Run
    assert _run_store("auto", "proj", "") == "local"                # laptop
    assert _run_store("off", "proj", "x") == "off"
