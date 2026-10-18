# Trend2Line

**Should our factory make the food that's going viral right now, for which country, and how fast do we need to be?**

Trend2Line answers that in about a minute, and shows its working for every number it gives you.

Built for the Google Cloud AI Builder Cup 2026, theme *Manufacturing: Intelligent Operations & Industrial Efficiency*.

---

## The problem, in one story

A chocolate bar filled with pistachio cream and crispy pastry goes viral on social media. A food company's
product team asks the obvious question: *can we make this?*

Answering it today takes weeks of meetings, and it involves four hard questions at once:

1. **Is the hype still growing, or already fading?** Viral foods often lose half their buzz within weeks. If we
   launch after that, we've built something nobody is searching for anymore.
2. **Can our production lines make it?** Maybe we need a machine we don't have, and buying one takes months.
3. **Will it damage products we already sell?** If we run pistachio on the line that makes our "nut-free" kids'
   bar, that bar can no longer honestly be called nut-free. One trendy product can quietly break promises on
   several existing ones.
4. **What must the label say in each country?** Every country has its own list of allergens to declare. India
   doesn't require sesame to be flagged; Europe does. Australia wants "pistachio" written by name; Korea doesn't
   list it at all. The Gulf states want Arabic. A name like "Dubai chocolate" can count as a misleading claim
   about where the product comes from.

Get any one of these wrong and you either miss the trend, waste money, or put a mislabelled product on shelves.

## What Trend2Line does

You type a trend name (for example *Croffle*, *Tanghulu* or *Dubai chocolate*), choose a target country, and press
**Run analysis**. You get:

| You see | What it means |
|---|---|
| **GO / CONDITIONAL / NO-GO** | The overall answer |
| **"On shelf by 31 March"** | The deadline: after this date, less than half of today's interest is left |
| **Days to spare or days too late** | Your planned launch compared with that deadline |
| **Best production line** | Which line can make it, and what equipment is missing |
| **Allergen blast radius** | Which existing products would lose a "free-from" claim, and why |
| **What the label must say** | The allergens this country requires, the language, and any rules triggered, each with a link to the official source |
| **Market scan** | All 12 markets ranked, so you can see if another country is a better bet |
| **Written recommendation** | A short summary, where every number has been fact-checked |
| **Where every input came from** | Which parts are real public data and which are sample company data |

## How it works, in plain words

Think of it as a team of four:

- **A researcher** that reads public data (how much people look a trend up, what's in a recipe, the population
  and income of each country, the labelling laws).
- **A calculator** that does every piece of maths: deadlines, timelines, which line fits, which products are put
  at risk. It never guesses.
- **A writer** (Google's Gemini AI) that turns the results into a readable recommendation.
- **A fact-checker** that checks every number and claim the writer makes against the calculator's results. If the
  writer gets something wrong, it's sent back to fix it. You can see what was caught.

The key rule: **the AI interprets and writes; it never decides.** The GO/NO-GO verdict, every date and every number
come from the calculator, so the AI can't make up a convenient answer.

### The steps, one by one

1. **How hot is the trend, in this country?** We look at how many people read about the food on Wikipedia each
   day, in the country's own language: Japanese Wikipedia for Japan, Arabic for the Gulf, Portuguese for Brazil.
   This is free, public and updated daily. It's a stand-in for public interest, not sales.
2. **How fast do trends like this fade?** We measured how quickly about 30 past food fads lost half their
   interest (their "half-life"). That gives the deadline: *be on shelf within N days to keep half the buzz.*
3. **What's actually in it?** Gemini describes the product a factory would make: ingredients and production
   steps. If you upload a photo, it uses that too.
4. **Which allergens does that mean?** We look every ingredient up in the Open Food Facts database, a free global
   food database. It knows that pistachio paste comes from pistachios, which are tree nuts. It handles names in
   many languages, and flags anything it can't match instead of quietly ignoring it.
5. **Can our lines make it, and what does it break?** We compare the product's steps and allergens with each
   production line and every product already made on it.
6. **How long until it's on shelves?** We map out the tasks (trials, label design, cleaning checks, translations)
   and find the longest chain.
7. **What must the label say?** We check the product against that country's labelling rules, including rules that
   start on a future date, like China's in March 2027.
8. **Write it up and fact-check it.** Gemini writes; the fact-checker verifies.

## Where the data comes from

| Information | Source | Real or sample? |
|---|---|---|
| Interest in a trend, by country | Wikipedia page views, in each country's language | Real, live |
| How fast fads fade | Wikipedia page views of ~30 past food fads | Real (computed when you deploy) |
| Ingredients and allergens | Open Food Facts (free global food database) | Real |
| Labelling rules for 12 markets | Official regulator sites and reputable summaries, each with a link and a "how sure are we" badge | Researched |
| Country population and income | World Bank | Real |
| Place names (for "misleading origin" checks) | GeoNames world cities list | Real |
| Our factory, lines and products | A made-up sample company | **Sample** |
| How long tasks take, equipment lead times | Sample figures | **Sample** |

**About the sample company.** No company publishes its factory details, so the factory side uses an illustrative
plant with chocolate, bakery, dairy/drinks and snack lines. A real company would upload its own file (there's a
button for it) or connect its ERP system. The deadline, allergen and labelling parts work on public data alone,
so they're useful even without any company data.

## A worked example: Dubai-style chocolate in the Gulf

- **Deadline:** be on shelf within about 30 days to keep half of today's interest.
- **Plan:** our best line needs 62 days, so we'd be about a month late. Verdict: NO-GO.
- **Blast radius:** running it on line L2 would cost the "Kids Nut-Free Bar" its nut-free claim. The recommended
  line, L3, already handles nuts.
- **Label in the Gulf:** declare gluten, tree nuts, milk and sesame; label in Arabic; and review the word
  "Dubai", because a place name can mislead about where the product was made.
- **Same product, other countries:** India doesn't require sesame to be flagged, Australia wants "pistachio" by
  name, and China's rules only become mandatory in 2027.

(Exact numbers depend on the run date and on live data.)

## Markets covered

India, European Union, United Kingdom, United States, Canada, Australia & New Zealand, Japan, South Korea, China,
Singapore, the Gulf states (UAE, Saudi Arabia, Kuwait, Qatar, Bahrain, Oman) and Brazil.

## How Google Cloud is used

| Google service | Its job here, in one line |
|---|---|
| **Cloud Run** | Runs the website and the analysis; starts when someone visits, sleeps when no one does |
| **Cloud Build** | Turns the code into a running app each time you deploy, and refreshes the public data at the same time |
| **Artifact Registry** | Stores the packaged app between deploys |
| **Secret Manager** | Keeps the Gemini key safe, out of the code |
| **Gemini** (through Vertex AI or AI Studio) | Describes the product and writes the recommendation, 2 to 5 calls per analysis |
| **Cloud Logging** | Records what happened, for troubleshooting |
| **BigQuery** (optional, not used by the app) | A script you can run by hand to discover rising searches by country |

**`SETUP.md`** explains exactly when each one is called and how to set it up.

## What it isn't

- **Not legal advice.** It tells you which rules apply and where to read them. Rules marked "needs review" should
  be checked by a regulatory specialist before a real launch.
- **Not a sales forecast.** Wikipedia views show public curiosity, not purchases. English Wikipedia is read
  worldwide, so it can't tell US interest from Indian interest; the app labels this.
- **Not your real factory** until you upload your own plant file.

## Words you'll see

| Word | Meaning |
|---|---|
| **Half-life** | How long a trend takes to lose half its interest |
| **Free-from claim** | A promise on a pack such as "nut-free" or "gluten-free" |
| **Allergen blast radius** | Existing products that would lose a free-from claim if the new product shares their line |
| **Co-manufacturer** | Another company that makes the product for you |
| **Capture** | The share of today's interest still left on launch day |
| **Trust score** | How many of the writer's claims the fact-checker could verify (1 = all) |
| **Verified official / secondary / needs review** | How sure we are about a labelling rule's source |

## Project map

```
app/        the application (the researcher, calculator and fact-checker)
data/       all the facts the app relies on: markets, ingredients, sample plant
static/     the web page
scripts/    data refresh and health checks (start with: python -m scripts.doctor)
tests/      automatic checks (32 of them)
docs/TECHNICAL.md   developer details
SETUP.md    Google Cloud setup and when each service is called
ROADMAP.md  what to build next
```
