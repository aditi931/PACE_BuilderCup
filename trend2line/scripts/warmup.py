"""Warm up a deployed Trend2Line before a demo, and check the demo scenarios still give their verdicts.

    python3 scripts/warmup.py --url https://<service>.run.app          (deploy.sh runs this after deploying)

Runs every scenario from /api/config once, so the instance's Wikipedia cache, Gemini cache and the "Recent
decisions" list are filled before anyone watches. Standard library only, so it runs in Cloud Shell as is.
Exit code 1 if the service is down, a scenario fails, or a verdict differs from data/demo_scenarios.yaml.
"""
import sys as _sys

if hasattr(_sys.stdout, "reconfigure"):  # Windows consoles: print non-ASCII safely
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def call(url: str, data: dict | None = None, timeout: int = 300) -> tuple[int, dict]:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {"detail": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="service URL, e.g. https://trend2line-xyz.a.run.app")
    ap.add_argument("--retries", type=int, default=2, help="retries per scenario (Wikipedia can be busy)")
    a = ap.parse_args()
    base = a.url.rstrip("/")

    code, cfg = call(f"{base}/api/config", timeout=60)
    if code != 200:
        print(f"FAIL  /api/config answered {code}: {cfg}")
        return 1
    ref = cfg.get("reference", {})
    print(f"mode {cfg.get('mode')} · fad reference {ref.get('status')} ({ref.get('n_fads')} fads) · "
          f"{len(cfg.get('companies', {}))} companies · {len(cfg.get('scenarios', []))} scenarios")
    ok = cfg.get("mode") == "live" and ref.get("status") == "computed"
    for s in cfg.get("scenarios", []):
        form = {k: s[k] for k in ("trend", "company", "sku_id", "market", "as_of")} | {"scan": "true"}
        for attempt in range(a.retries + 1):
            t0 = time.time()
            code, j = call(f"{base}/api/run", form)
            if code == 200 or attempt == a.retries:
                break
            print(f"  retrying {s['trend']} after HTTP {code}: {j.get('detail', '')[:120]}")
            time.sleep(10)
        took = time.time() - t0
        if code != 200:
            print(f"FAIL  {s['label']:<9} {s['trend']} · {s['market']}: HTTP {code} {j.get('detail', '')[:200]}")
            ok = False
            continue
        v = j["decision"]["verdict"]
        same = v == s["expected"]
        ok &= same
        print(f"{'OK  ' if same else 'DIFF'}  {s['label']:<9} {s['trend']} · {s['market']} · {s['as_of']}: {v} "
              f"(expected {s['expected']}), {round(j['decision']['capture_median'] * 100)}% interest left, "
              f"{j['decision']['days_to_shelf']} days to shelf, {took:.0f}s")
    print("Ready for the demo." if ok else "Check the lines above before the demo.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
