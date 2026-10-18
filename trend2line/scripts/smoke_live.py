"""Hit every live data source once and report. Run before a demo:  python -m scripts.smoke_live"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):  # Windows consoles: print non-English titles safely
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import datetime as dt

from app.config import get_settings
from app.stages import trend as T


def main() -> None:
    s = get_settings()
    http = T.Http(s.wiki_user_agent, None)
    end = dt.date.today() - dt.timedelta(days=2)
    checks = []
    try:
        cands = T.search_candidates(http, "Dubai chocolate", "en")
        checks.append(("Wikipedia search (en)", bool(cands), cands[:3]))
        qid = T.wikidata_qid(http, cands[0], "en")
        checks.append(("Wikidata QID", bool(qid), qid))
        links = T.sitelinks(http, qid)
        checks.append(("Wikidata sitelinks", len(links) > 1, sorted(links)[:12]))
        for lang in ("en", "ja", "ar", "de", "ko"):
            if lang in links:
                pv = T.fetch_pageviews(http, links[lang], end - dt.timedelta(days=30), end, lang)
                checks.append((f"Pageviews {lang}:{links[lang]}", bool(pv), f"{len(pv)} days, last {pv[-1] if pv else '-'}"))
    except Exception as exc:
        checks.append(("live sources", False, str(exc)))
    for name, ok, detail in checks:
        print(f"{'OK ' if ok else 'FAIL'}  {name}: {detail}")


if __name__ == "__main__":
    main()
