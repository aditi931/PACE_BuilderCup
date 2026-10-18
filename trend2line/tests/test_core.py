import datetime as dt
import io
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
