# tr-macro-mcp

An [MCP](https://modelcontextprotocol.io) server that gives LLM clients (Claude Desktop, Claude
Code, Cursor, ...) structured, **cited** access to Turkish macroeconomic data from the Central
Bank of the Republic of Türkiye (TCMB).

- **Works without any API key**: official TCMB exchange rates for any business day since 1996,
  and TCMB documents (MPC rate decisions, Inflation Reports, Financial Stability Reports, Survey
  of Market Participants) in Turkish and English, with page-level citations.
- **Optional free EVDS key** unlocks the EVDS catalogue (policy rates, inflation, reserves, ...).
- **Every result carries citations** (source, URL, date, bulletin number, retrieval time). Missing
  data is reported as missing, never guessed or silently replaced by another date.

> Status: Phase 2 of 6 (numeric data + documents). Cross-source tools, the evaluation harness
> and RAG mode follow. See [PLAN.md](PLAN.md).

> **Disclaimer.** Data and cited summaries only; **not investment advice**. Data © TCMB, used
> with attribution under [TCMB's terms](https://tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Bottom+Menu/Diger/Kullanim+Sartlari);
> commercial use of TCMB content requires TCMB's written permission. This is an independent,
> non-commercial open-source project, not affiliated with TCMB.

## Quickstart (keyless)

Requirements: [uv](https://docs.astral.sh/uv/) (it installs a suitable Python for you).

```bash
git clone <this repo> tr-macro-mcp
cd tr-macro-mcp
uv sync
uv run tr-macro-mcp ingest # one-off: download and index TCMB documents (~10-15 min, ~150 MB)
uv run tr-macro-mcp        # starts the stdio server (Ctrl+C to stop); clients normally launch it
```

`ingest` is polite (1 request/s, descriptive User-Agent) and idempotent: re-run it any time to
pick up new publications; it only re-reads index pages and downloads what is new. Narrow it with
`--types mpc,ir,fsr,smp`, `--languages tr,en`, `--mpc-since 2014`, `--reports-since 2022`.
The server never downloads documents during a conversation; it reads the local corpus.
After upgrading tr-macro-mcp, `uv run tr-macro-mcp reparse` rebuilds the text index from the
already downloaded PDFs (no network).

### Connect Claude Code

```bash
claude mcp add --transport stdio tr-macro -- uv --directory /absolute/path/to/tr-macro-mcp run tr-macro-mcp
```

### Connect Claude Desktop

Edit `claude_desktop_config.json` (Windows: `%APPDATA%\Claude\claude_desktop_config.json`,
macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`), then fully restart
Claude Desktop:

```json
{
  "mcpServers": {
    "tr-macro": {
      "command": "uv",
      "args": ["--directory", "C:\\absolute\\path\\to\\tr-macro-mcp", "run", "tr-macro-mcp"]
    }
  }
}
```

If Claude Desktop cannot find `uv`, use its full path (`where uv` on Windows, `which uv`
elsewhere). Server logs: `%APPDATA%\Claude\logs\mcp-server-tr-macro.log`.

Then ask, for example: *"What was the USD/TRY rate on 2 January 2025?"* or *"USD/TRY on
1 January 2025?"* (a holiday: the answer says no bulletin was published and names the closest
earlier one).

## EVDS key (optional upgrade)

1. Register at [evds3.tcmb.gov.tr](https://evds3.tcmb.gov.tr), log in, open **Profile** and
   click **Copy API Key**.
2. `cp .env.example .env` and set `EVDS_API_KEY=...` in `.env` (git-ignored). The `--directory`
   argument above makes the server read this file. Alternatively pass it as an environment
   variable: `claude mcp add --transport stdio --env EVDS_API_KEY=... tr-macro -- uv ...`, or an
   `"env": {"EVDS_API_KEY": "..."}` entry in the Claude Desktop config.
3. Restart the client and ask: *"Find the EVDS series for the policy rate and give me its value
   on <date>."*

Without a key, EVDS tools return `EVDS_API_KEY not set; see README` instead of failing.

## Tools

| Tool | Key? | What it does |
|---|---|---|
| `list_data_sources` | no | Sources, coverage, and whether each is available in this session |
| `get_fx_rate(currency, date?, rate_type?)` | no | TCMB rate for one bulletin date; explicit `not_published` for weekends/holidays |
| `search_series(query)` | EVDS | Find EVDS series codes by Turkish/English keywords |
| `get_series(series_code, start, end, frequency?, aggregation?, source?)` | EVDS, or no for `FX:<CUR>` | Observations + metadata + citations |
| `compare_periods(series_code, period A, period B)` | as above | Descriptive change statistics between two periods |
| `list_documents(doc_type?, language?, date_from?, date_to?, query?)` | no | Local TCMB documents, newest first |
| `get_document_outline(doc_id)` | no | Sections with PDF page and printed page number |
| `read_document(doc_id, page_start, page_end?)` | no | Page text (max 10 pages / 40k chars per call) with page citations |
| `grep_documents(pattern, regex?, filters...)` | no | Page-level matches across documents, newest first |

Document text is always returned between `BEGIN/END UNTRUSTED DOCUMENT TEXT` markers. Lines that
read like instructions to an AI (prompt injection) are removed and reported in
`security_notices`.

Keyless FX series codes: `FX:USD`, `FX:EUR:forex_selling`, ... (`rate_type` is one of
`forex_buying` (default), `forex_selling`, `banknote_buying`, `banknote_selling`). Keyless
series fetch one bulletin per day, so ranges are capped (daily 62 days, weekly 1 year, monthly
5 years); everything is cached locally afterwards.

## Configuration

See [.env.example](.env.example): `EVDS_API_KEY`, `TR_MACRO_CACHE_DIR` (default: OS user cache
dir), `TR_MACRO_MIN_REQUEST_INTERVAL` (default 1 s between requests per host),
`TR_MACRO_HTTP_TIMEOUT`.

## Development

```bash
uv sync --extra dev
uv run pytest                    # offline, recorded fixtures, no key needed
uv run pytest -m live            # live smoke test against TCMB (network)
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run python scripts/record_fx_fixtures.py      # re-record TCMB XML fixtures
uv run python scripts/record_evds_fixtures.py    # record real EVDS fixtures (needs key)
uv run python scripts/record_doc_fixtures.py     # re-record TCMB document fixtures
uv run python scripts/make_poisoned_pdf.py       # regenerate the prompt-injection test PDF
```

Verified facts about the data sources (URLs, formats, limits, terms) are in
[docs/data-sources.md](docs/data-sources.md); design decisions in [docs/decisions/](docs/decisions/).

## Limitations

- TCMB publishes FX bulletins on business days around 15:30 Istanbul time; earlier that day,
  today's rate is `not_published`.
- Bulletins before 2005 are in old lira (TRL); values are converted at 1 TRY = 1,000,000 TRL and
  flagged.
- EVDS has no unit field; units are usually stated in the series name.
- Documents: text only. Charts and most "tables" in the reports are graphics, so their numbers
  are not reliably extractable. Outlines come from each report's Contents page (TCMB PDFs have
  no bookmarks); MPC decisions are 1-2 pages and have no outline.
- TCMB publishes only the latest Survey of Market Participants report; the corpus keeps a
  snapshot each time `ingest` runs in a new month. History is in EVDS (key).
- The EVDS observation parser was written against TCMB's documentation and its reference
  client, and is not yet validated against recorded live responses (pending a key; see
  `scripts/record_evds_fixtures.py`).

## License

MIT (code). Data belongs to TCMB; see the disclaimer above.
