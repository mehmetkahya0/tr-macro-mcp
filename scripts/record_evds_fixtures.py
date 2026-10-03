"""Record real EVDS responses for the offline tests. Needs EVDS_API_KEY (env or .env).

Run: uv run python scripts/record_evds_fixtures.py
Writes tests/fixtures/evds/recorded/*.json. Only response bodies are saved; the key is sent
as a header and never written. Series/datagroup codes are the examples from TCMB's own
web-service guide (docs/data-sources.md §2).
"""

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from tr_macro_mcp.config import USER_AGENT, Settings
from tr_macro_mcp.datasources.evds import BASE_URL

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "evds" / "recorded"
REQUESTS = {
    "categories": "categories/type=json",
    "datagroups": "datagroups/mode=0&type=json",
    "serielist_bie_yssk": "serieList/type=json&code=bie_yssk",
    "serielist_TP.DK.USD.A.YTL": "serieList/type=json&code=TP.DK.USD.A.YTL",
    "series_TP.DK.USD.A.YTL__daily": (
        "series=TP.DK.USD.A.YTL&startDate=30-12-2024&endDate=03-01-2025&type=json"
    ),
    "series_TP.DK.USD.A.YTL__monthly_avg": (
        "series=TP.DK.USD.A.YTL&startDate=01-01-2025&endDate=31-03-2025&type=json"
        "&frequency=5&aggregationTypes=avg"
    ),
}


def shape(value: Any, depth: int = 0) -> str:
    if isinstance(value, dict):
        inner = ", ".join(f"{k}: {shape(v, depth + 1)}" for k, v in list(value.items())[:8])
        return "{" + inner + "}"
    if isinstance(value, list):
        return f"[{len(value)} x {shape(value[0], depth + 1) if value else '-'}]"
    return type(value).__name__


def main() -> None:
    settings = Settings()
    if not settings.has_evds_key or settings.evds_api_key is None:
        sys.exit("EVDS_API_KEY is not set (env or .env).")
    key = settings.evds_api_key.get_secret_value()
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT, "key": key}, timeout=60) as client:
        for name, path in REQUESTS.items():
            response = client.get(BASE_URL + path)
            print(f"{name}: HTTP {response.status_code} {response.headers.get('content-type')}")
            response.raise_for_status()
            payload = response.json()
            if name == "datagroups" and isinstance(payload, list):
                print(f"  {len(payload)} datagroups; keeping the first 40")
                payload = payload[:40]
            (OUT / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
            )
            print("  shape:", shape(payload)[:400])
            time.sleep(1)


if __name__ == "__main__":
    main()
