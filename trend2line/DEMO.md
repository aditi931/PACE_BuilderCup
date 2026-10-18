# Demo script: three trends, three verdicts

Three ready-made combinations, one per outcome. Each runs on **real Wikipedia pageviews** for the date shown, a
**reviewed product spec** (`data/demo_trends.yaml`) and a **synthetic demo company** (`data/companies/`). Nothing
is a stored answer: the verdict is recomputed by code on every run.

| # | Outcome | Trend | Company | Compare against product | Market | Run as of |
|---|---|---|---|---|---|---|
| 0 | **GO** (positive) | Tanghulu | Hanbit Foods Co., Ltd. (demo) | Pumpkin Yeot Hard Candy 80 g (HB-C1) | South Korea | 2023-08-01 |
| 1 | **CONDITIONAL** (neutral) | Dubai chocolate | Kaveri Dairy & Desserts Pvt Ltd (demo) | Kesar Pista Kulfi Stick 60 ml (IC2) | India | 2025-04-15 |
| 2 | **NO-GO** (negative) | Dalgona coffee | Hanbit Foods Co., Ltd. (demo) | Jeju Milk Chocolate 45 g (HB-C2) | South Korea | 2020-07-01 |

Results measured on 2026-10-18 with the code in this repo:

| # | Verdict | Interest left at launch | Time to shelf | Shelf deadline | Why |
|---|---|---|---|---|---|
| 0 | GO | 71% | 41 days, in-house on HB-C1 | 83 days (42 to spare) | Rising in Korean Wikipedia; slow-fading fad; candy line already has kettle, cooling tunnel and tray sealer; no new allergens; label already Korean |
| 1 | CONDITIONAL | 63% | 55 days, co-manufacturer | 83 days | Strong, rising demand, but Kaveri has no chocolate line and the best in-house line would void nut-free / gluten-free claims on existing products (hard constraint). Shows the real launches tab (Läderach, ITC Fabelle, Cadbury in India) |
| 2 | NO-GO | 20% | 55 days, co-manufacturer | 24 days (31 days too late) | Flash fad already decaying (24-day half-life); Hanbit has no beverage line |

## How to run them

**In the UI:** click a card under *Demo scenarios* (it fills the form and runs). Or open a deep link:

```
https://<service-url>/#scenario=0          GO
https://<service-url>/#scenario=1          CONDITIONAL
https://<service-url>/#scenario=2          NO-GO
https://<service-url>/#scenario=1&tab=production   open on a tab: overview | markets | production | evidence | audit
```

**With curl:**

```bash
URL=https://pace-buildercup-241790677133.asia-south1.run.app
curl -s -X POST $URL/api/run -F trend=Tanghulu -F company=hanbit_foods -F sku_id=HB-HOBAKYEOT-80 \
     -F market=KR -F as_of=2023-08-01 | jq .decision.verdict          # "GO"
curl -s -X POST $URL/api/run -F "trend=Dubai chocolate" -F company=kaveri_dairy -F sku_id=KV-KULFI-PISTA-60 \
     -F market=IN -F as_of=2025-04-15 | jq .decision.verdict          # "CONDITIONAL"
curl -s -X POST $URL/api/run -F "trend=Dalgona coffee" -F company=hanbit_foods -F sku_id=HB-MILKCHOC-45 \
     -F market=KR -F as_of=2020-07-01 | jq .decision.verdict          # "NO-GO"
```

## Before the demo

- `deploy.sh` runs `scripts/warmup.py` after every deploy: it runs the three scenarios once (fills the caches and
  *Recent decisions*) and checks each verdict. Run it again on demo morning:
  `python3 scripts/warmup.py --url https://pace-buildercup-241790677133.asia-south1.run.app`
- Fill the **Opportunity watch** once so the home page has it:
  `gcloud scheduler jobs run <service>-watch --project <project> --location asia-south1` (it then runs daily).
- Deploy with `MIN_INSTANCES=1` on demo day so the first click doesn't wait for a cold start.

## A 5-minute walkthrough

**1. Home (30 s).** "Every morning the app has already run today's rising food searches against every company."
Point at **Opportunity watch**, then the three **Demo scenarios**.

**2. GO: Tanghulu for Hanbit Foods in Korea (60 s).** Click the green card.
- *Verdict hero*: 71% of interest left, on shelf in 41 days vs an 83-day deadline.
- *Impact*: decision in seconds vs ~3 weeks of review; no free-from claim at risk.
- *Ask Trend2Line*: click "Why this verdict?" and show the "verified" tag under the answer.

**3. CONDITIONAL: Dubai chocolate for Kaveri Dairy in India (90 s).** Click the amber card.
- *Hard constraint*: every in-house line would void nut-free or gluten-free claims (Overview → blast radius).
- *What-if*: click an in-house line to show the claims it voids; click the co-packer to show it is the way through.
- *Markets & labelling → Draft the label*: pick Hindi; show the code checks (every declaration covered, right script).
- *Real-world evidence*: Läderach, ITC Fabelle and Cadbury launched in India; where each landed on the curve.
- *Compare → Across companies*: the same trend is a different answer for each factory.

**4. NO-GO: Dalgona coffee for Hanbit Foods in Korea (60 s).** Click the red card.
- 31 days past the deadline with 20% of interest left: a fast fad and no beverage line.
- *What-if*: drag to "30 days faster": even a co-packer that finds a month only reaches CONDITIONAL (49% of
  interest, one day past the GO deadline).

**5. Close (30 s).** *Decision brief ↗* opens the one-pager for the product committee. *Audit trail* shows where
every input came from. "The AI interprets and writes; code decides and checks."

## Everything on a results page

| Where | What |
|---|---|
| Verdict hero | Interest gauge, shelf deadline, time to shelf, days to spare / too late, launch-vs-deadline strip |
| Overview | Impact panel, Gemini's recommendation, **Ask Trend2Line**, interest curve, blast radius, claims and receipts |
| What-if | Every plan (co-packer, each line, real companies' pace) × fad speed × expedite/delay |
| Compare | Same trend for every company, or for this company across the top markets |
| Markets & labelling | 12 markets ranked; declarations and rules with sources; **Draft the label** |
| Production | Critical-path timeline, line feasibility, ingredients and allergens |
| Real-world evidence | Real launches (Dubai chocolate, tanghulu, Dalgona coffee), observed trend windows, model check |
| Audit trail | Provenance of every input, run log, Gemini calls |
| Toolbar | **Decision brief ↗** (print / PDF) |

## Notes

- The demo companies are fictional and labelled "demo" everywhere. Their plant files are used exactly like an
  uploaded plant file, so the "sample plant" caveat does not apply to them.
- Live runs also call Gemini (article choice, write-up) and Google Search grounding (launch discovery). Those can
  change the wording and the evidence list, never the verdict: the verdict is decided in code before the writer runs.
- Change the company, market or date of any scenario to show how each input moves the answer (for example the
  same Tanghulu run for Kaveri Dairy in India, read from shared English Wikipedia).
