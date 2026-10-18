"""OPTIONAL, UNTESTED HERE: discover rising food searches per country from Google's public BigQuery dataset.

bigquery-public-data.google_trends.international_top_rising_terms holds the daily top 25 rising search terms
for about 50 countries (US data is in the separate top_rising_terms table). It is good for DISCOVERY
("what food is rising in Indonesia this week?"), not for tracking a niche term, because only the top 25 are kept.

    pip install google-cloud-bigquery
    gcloud auth application-default login
    python -m scripts.discover_trends_bq --project YOUR_PROJECT --countries ID,MY,TH,AE --days 14

Feed the printed terms to Trend2Line, or let Gemini filter them for food items.
"""
import argparse

SQL = """
SELECT country_code, term, MAX(percent_gain) AS max_gain, MIN(rank) AS best_rank, MAX(refresh_date) AS last_seen
FROM `bigquery-public-data.google_trends.international_top_rising_terms`
WHERE refresh_date >= DATE_SUB(CURRENT_DATE(), INTERVAL @days DAY)
  AND country_code IN UNNEST(@countries)
GROUP BY country_code, term
ORDER BY country_code, max_gain DESC
LIMIT 500
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--countries", default="IN,ID,AE,SA,JP,KR,BR")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    from google.cloud import bigquery  # optional dependency
    client = bigquery.Client(project=a.project)
    job = client.query(SQL, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("days", "INT64", a.days),
        bigquery.ArrayQueryParameter("countries", "STRING", a.countries.split(",")),
    ]))
    for row in job.result():
        print(f"{row.country_code}  {row.term:<40} gain {row.max_gain}  best rank {row.best_rank}  {row.last_seen}")


if __name__ == "__main__":
    main()
