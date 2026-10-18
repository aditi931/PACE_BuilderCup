# Trend2Line roadmap

## Where it stands (v0.2)

Real open data for trends (multilingual Wikipedia), ingredients and allergens (Open Food Facts), market size
(World Bank) and place names (GeoNames); 12 markets of researched regulations with sources; a market scan; a
deterministic verdict; a Gemini write-up that is checked claim by claim. Factory data is sample input that a
company replaces with its own.

## Demo script (real companies)

1. Pick **Cadbury (India)** in the Company menu, trend **Dubai chocolate**, market **India**, today's date.
2. Show the deadline, then **Real-world evidence**: the trend's TikTok origin, India's interest window, and where
   Läderach, Fabelle and Cadbury launched on it. Real time to market drives the verdict.
3. Show the allergen blast radius across Cadbury's real products, and India's labelling.
4. Switch the market to the Gulf: Arabic label, the "Dubai" origin-claim check, sesame.
5. Show the model check and the Lindt pack validation as proof the engine matches reality.
6. Hand judges the case-study report from `scripts.case_study`.

## Before the demo (days)

1. Deploy once so Cloud Build computes `data/fad_reference.json` from real pageviews; this lifts the CONDITIONAL cap.
2. Have a regulatory person confirm every `needs_review` rule, starting with the Gulf (GSO 9:2022, GSO 2810:2025)
   and the EU/US gluten-free and origin-claim rules. Flip their status in `data/markets.yaml`.
3. Run `scripts.build_company_profile --all` and `scripts.case_study` with internet access and check the numbers.
4. Add real launches for one or two more trends (e.g. Dalgona coffee, Tanghulu) in `data/reference_launches.yaml`,
   each with a source.
5. Rehearse three contrasting runs: Dubai chocolate for the Gulf (origin claim, Arabic label, sesame),
   Croffle for Korea (the bakery line would void an egg-free claim), and Dalgona coffee for India.

## Better demand signals (weeks)

| Source | What it adds | Notes |
|---|---|---|
| Google Trends API (alpha) | Consistently scaled search interest by country and region, 5 years back | Apply for alpha access now; plug in behind `app/stages/trend.py` |
| BigQuery `google_trends` public dataset | Daily top-25 rising searches in ~50 countries | Discovery, not tracking; see `scripts/discover_trends_bq.py` |
| Open Food Facts product data | How many similar products already exist per country, and how fast new ones appear | Public API and full dumps; a competitor-saturation signal |
| Company data | Own sell-out, retailer search terms, social listening | Same interface; this is where a customer's moat lives |

## Factory and cost (months)

- ERP/MES connectors (e.g. SAP equipment master, SKU master, line schedules) replace `plant.yaml`.
- Capacity: check free hours on the chosen line, not just capability.
- Cost and margin: ingredient prices (World Bank commodity prices, FAO food price index) for a rough unit cost.
- Allergen changeover cost and cleaning-validation history from the plant's own records.

## Regulation depth

- More markets: Indonesia (BPOM, halal), Malaysia, Thailand, Vietnam, Mexico, Türkiye, Egypt, Nigeria, South Africa.
- Beyond allergens: additives and colours that differ by market (for example titanium dioxide in the EU),
  halal certification for the Gulf, Indonesia and Malaysia, mandatory front-of-pack schemes.
- Generate a draft allergen statement in each market's language, for a human to approve.
- Precautionary "may contain" thresholds (VITAL reference doses) instead of yes/no.

## Product

- ADK multi-agent version (`adk_app/`) with each stage as a sub-agent and traces for judges.
- Portfolio view: run every rising trend against every plant nightly and alert when a window opens.
- Feedback loop: record which recommendations launched and how they sold, and recalibrate half-lives per category.

## Which markets to target first

Lead with three contrasting segments that show different strengths of the tool:

- **Gulf states**: high income, fast trend adoption, Arabic labelling and a broad allergen list.
- **India**: the home market, very large and price-sensitive, with a short allergen list (no sesame).
- **Japan and South Korea**: trend origins with very specific allergen lists (buckwheat, walnut, cashew, pine
  nut), which shows off ingredient-lineage matching.

Next: Southeast Asia (Indonesia's scale, Singapore as a gateway) and Brazil. Use the in-app market scan with your
own weights in `data/scoring.yaml` to rank them for each specific trend rather than in general.
