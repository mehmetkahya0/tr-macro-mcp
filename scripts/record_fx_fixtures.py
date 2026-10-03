"""Record TCMB FX XML bulletins used by the offline tests.

Run: uv run python scripts/record_fx_fixtures.py
Writes tests/fixtures/tcmb_fx/<DDMMYYYY>.xml (raw bytes) and manifest.json (status per date).
Data © TCMB, stored as small attributed test fixtures (see docs/data-sources.md).
"""

import datetime as dt
import json
import time
from pathlib import Path

import httpx

from tr_macro_mcp.config import USER_AGENT
from tr_macro_mcp.datasources.tcmb_fx_xml import TODAY_URL, bulletin_url

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "tcmb_fx"
DATES = [
    dt.date(2004, 1, 2),  # pre-redenomination (TRL), ISO-8859-9
    dt.date(2024, 12, 30),
    dt.date(2024, 12, 31),
    dt.date(2025, 1, 1),  # New Year: no bulletin
    dt.date(2025, 1, 2),
    dt.date(2025, 1, 3),
    dt.date(2025, 1, 4),  # Saturday: no bulletin
    dt.date(2025, 1, 31),
    dt.date(2025, 2, 28),
    dt.date(2025, 3, 28),
    dt.date(2025, 3, 31),  # Ramazan Bayramı: no bulletin
    # Week of the 23 July 2026 MPC meeting (explain_move tests)
    *(dt.date(2026, 7, d) for d in range(20, 25)),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, int] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        for day in DATES:
            response = client.get(bulletin_url(day))
            manifest[day.isoformat()] = response.status_code
            if response.status_code == 200:
                (OUT / f"{day:%d%m%Y}.xml").write_bytes(response.content)
            elif response.status_code == 404:
                (OUT / "not_found.html").write_bytes(response.content)
            time.sleep(1)
        response = client.get(TODAY_URL)
        response.raise_for_status()
        (OUT / "today.xml").write_bytes(response.content)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
