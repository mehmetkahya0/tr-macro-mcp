# Data sources — verified facts

Everything below was checked against live responses or official documents on **2026-10-03**
(Saturday) from Istanbul time, unless a different date is given. When something was not
verified, it says so. Re-verify before relying on anything marked *unverified*.

User-Agent used for all checks: `tr-macro-mcp/0.0 (research)`. Requests were spaced ≥0.7 s apart.

---

## 1. TCMB daily exchange rates (XML) — keyless

### Endpoints (verified)
| Purpose | URL pattern | Example |
|---|---|---|
| Latest bulletin | `https://www.tcmb.gov.tr/kurlar/today.xml` | — |
| Historical bulletin | `https://www.tcmb.gov.tr/kurlar/{YYYYMM}/{DDMMYYYY}.xml` | `https://www.tcmb.gov.tr/kurlar/202501/02012025.xml` |

- No authentication, no key.
- `today.xml` is **the latest published bulletin, not today's date**. On Saturday 2026-10-03
  it returned `Tarih="02.10.2026"` with `Last-Modified: Fri, 02 Oct 2026 12:30:02 GMT`
  (= 15:30 Istanbul time). The response's own `Tarih` attribute is the authoritative date.
- Publication time: `Last-Modified` was 12:30 GMT (15:30 TRT) on every file checked
  (2025-01-02 → `12:30:01 GMT`, 2026-10-02 → `12:30:02 GMT`). Before ~15:30 TRT on a business
  day, that day's file does not exist yet. *The TCMB docs on whether these rates are "indicative
  rates for the next day" were not checked; we will cite the bulletin date only and not
  interpret it.*
- Responses carry `ETag` and `Last-Modified` → conditional requests (`If-None-Match`) are possible.

### Non-publication days (verified)
A missing bulletin returns **HTTP 404 with an HTML error page** (not XML):

| Date | Day | Result |
|---|---|---|
| 2025-01-01 | New Year | 404 |
| 2025-01-04 | Saturday | 404 |
| 2025-03-31 | Ramazan Bayramı | 404 |
| 2025-06-06 | Kurban Bayramı | 404 |
| 2023-10-27 | Friday (before Republic Day) | 200 |
| 2023-10-30 | Monday (Republic Day eve) | 200 |

Implication: 404 is the only signal we get. We cannot distinguish "holiday" from "not yet
published" from "URL wrong" by status code alone, so the tool will say "no bulletin was
published for that date" and offer the **nearest earlier published date as a separate,
labelled field**. It will never return a different date's rate as if it were the requested date.

### Archive depth (verified)
- 1996-04-01 → 404; 1996-06-03 → 200; 1997-01-02 → 200. The archive starts between April and
  June 1996. (Another server claims "since 1996"; the January 1996 probes were 404.)
- **Pre-2005 files are in old Turkish lira (TRL)**: 2004-01-02 USD ForexBuying = `1389282`.
  The 2005 redenomination is 1 TRY = 1,000,000 TRL. Pre-2005 files are also encoded
  **ISO-8859-9** (newer ones are UTF-8) and are minified on a single line per currency.
  Pre-2005 files have extra elements `<CrossRateEuro/>` and `<Parite/>`.

### Structure (verified, 2026-10-02 bulletin)
```xml
<Tarih_Date Tarih="02.10.2026" Date="10/02/2026" Bulten_No="2026/186">
  <Currency CrossOrder="0" Kod="USD" CurrencyCode="USD">
    <Unit>1</Unit>
    <Isim>ABD DOLARI</Isim>
    <CurrencyName>US DOLLAR</CurrencyName>
    <ForexBuying>48.9699</ForexBuying>
    <ForexSelling>49.0582</ForexSelling>
    <BanknoteBuying>48.9357</BanknoteBuying>
    <BanknoteSelling>49.1318</BanknoteSelling>
    <CrossRateUSD/>
    <CrossRateOther/>
  </Currency>
  ...
```
- 22 currencies on 2026-10-02: USD AUD DKK EUR GBP CHF SEK CAD KWD NOK SAR JPY RON RUB CNY PKR
  QAR KRW AZN AED KZT XDR.
- `Unit` is not always 1: **JPY Unit = 100**. Rates must be divided by `Unit`.
- Banknote rates are **empty** for RON, RUB, CNY, PKR, QAR, KRW, AZN, AED, KZT, XDR.
- `CrossRateUSD` (e.g. AUD 1.4409) / `CrossRateOther` (e.g. EUR 1.1248 = EUR/USD) are filled for
  some currencies only.
- XDR's `CurrencyName` has trailing whitespace → strip.
- `Bulten_No` (e.g. `2026/186`) is the bulletin number; include it in citations.
- Decimal separator is `.`.

### Terms of use
TCMB website terms ("Kullanım Şartları"),
https://tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Bottom+Menu/Diger/Kullanim+Sartlari :
> "Sitede yer alan bilgiler, kaynak gösterilmek suretiyle yayımlanabilir; ancak bu bilgilerin
> ticari amaçlarla kullanımı TCMB'nin yazılı iznine tabidir."
> (Information on the site may be published with attribution; commercial use requires TCMB's
> written permission.)

Also: TCMB holds copyright and accepts no liability for accuracy or availability.

`https://www.tcmb.gov.tr/robots.txt` contains only:
```
User-agent: *
Disallow: */search+results
```
Nothing we plan to fetch is disallowed.

**Consequences for us:** always attribute (citation per result); the project is
non-commercial open source and the README must say that commercial use of the data
needs TCMB's permission; cache aggressively (historical bulletins never change); ≥1 s between
requests; descriptive User-Agent.

---

## 2. TCMB EVDS (Electronic Data Delivery System) — requires a free key

### Endpoints (verified from the official guide + live probes)
Official guide: *EVDS Web Service and API Usage* (EN),
https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=16
(TR version: `docId=8`; Python guide: `docId=4` TR / `docId=13` EN).

Base: `https://evds3.tcmb.gov.tr/igmevdsms-dis/`

Parameters are written **in the path, joined by `&`, not as a normal `?` query string**:

| Service | URL |
|---|---|
| Observations | `series={CODE1-CODE2}&startDate=dd-mm-yyyy&endDate=dd-mm-yyyy&type=json[&aggregationTypes=..][&formulas=..][&frequency=N]` |
| All series in a data group | `datagroup={code}&startDate=..&endDate=..&type=json` |
| Topics (categories) | `categories/type=json` |
| Data groups | `datagroups/mode=0&type=json` (all), `datagroups/mode=1&code={code}&type=json` (one) |
| Series list | `serieList/type=json&code={datagroup or series code}` |

- **Auth:** API key in an **HTTP request header named `key`** (changed from a URL parameter on
  2024-04-05). The old host `evds2.tcmb.gov.tr` is no longer the API.
- Live probes:
  - no header → `403`, `application/json`: `{"status":"403","message":"Required request header 'key' is not present"}`
  - invalid key → `401`, `text/plain`: `Invalid API Key`
  - *Valid-key responses have not been recorded yet* (no key was available in Phase 0 or 1).
    Not shown in TCMB's guides either. **Secondary evidence only:** the `evds` PyPI client
    (v0.4.0, which TCMB's Python guide `docId=13` tells users to install) reads observations from
    `json["items"]`, where each item has `Tarih`, `UNIXTIME` and one column per series with
    dots replaced by underscores (`TP_DK_USD_A`), values as strings. It reads metadata as
    JSON lists with upper-case keys (`CATEGORY_ID`, `DATAGROUP_CODE`, `SERIE_CODE`, ...) and
    uses an undocumented `datagroups/mode=2&code={category_id}`. Our parser follows this, matches
    keys case-insensitively, and fails loudly on any other shape.
    `scripts/record_evds_fixtures.py` records real responses (including a cross-check of
    `TP.DK.USD.A.YTL` on 2025-01-02 against the XML bulletin's 35.2525) once a key exists.
- Key: register at evds3.tcmb.gov.tr → Profile → "Copy API Key".
- Formats: `csv`, `xml`, `json`. `decimalSeperator` (sic) `.` default.
- Dates: `dd-mm-yyyy`. For a converted frequency, `startDate` must be the first day of the
  period (e.g. 01-04-2025 for Q2), or the first period is dropped.
- **Limit: a maximum of 1000 observations per request.** Larger ranges silently return the
  last 1000 counted back from `endDate` → we must chunk requests and check that coverage is complete.
- The guide says data is "typically updated at a daily frequency… the data will be pulled once
  per day" → TTL ≥ 24 h for observations, longer for metadata.
- `aggregationTypes`: `avg|min|max|first|last|sum` (one per series, `-` separated).
- `formulas`: 0 level, 1 % change, 2 difference, 3 annual % change, 4 annual difference,
  5 % change vs previous year-end, 6 difference vs previous year-end, 7 moving average, 8 moving sum.
- `frequency`: 1 daily, 2 business day, 3 weekly, 4 bi-weekly, 5 monthly, 6 quarterly,
  7 semi-annual, 8 annual.
- Series metadata fields (serieList): `SERIE_CODE, DATAGROUP_CODE, SERIE_NAME, SERIE_NAME_ENG,
  FREQUENCY_STR, DEFAULT_AGG_METHOD(_STR), TAG, TAG_ENG, DATASOURCE(_ENG), METADATA_LINK(_ENG),
  REV_POL_LINK(_ENG), APP_CHA_LINK(_ENG), START_DATE, END_DATE`.
- **No keyword-search endpoint exists.** `search_series` must search a locally cached catalog
  (categories → datagroups → serieList), built once with the user's key and refreshed on a TTL.
- No published rate limit was found. *Unverified*; we will self-limit (≤1 req/s, back off on
  429/5xx).
- Series code for "the policy rate" is **not yet verified**. It will be looked up from the
  catalog in Phase 1 rather than hard-coded from memory.

### Terms of use (verified)
*EVDS Kullanım Şartları*, https://evds3.tcmb.gov.tr/igmevdsms-dis/documents/showDocument?docId=18
(EN disclaimer: `docId=21`). Users accept the TCMB website terms plus:
- "EVDS uygulamasında sunulan verilere, web servis aracılığıyla erişim sağlanabilir." — access via
  the web service is explicitly allowed.
- "…veriler kaynak gösterilmek koşuluyla üçüncü kişiler tarafından kullanılabilir ve
  yayımlanabilir." — usable and publishable by third parties **with attribution**.
- When data is translated into another language, it must be stated that **the translation is
  not an official TCMB translation**. (We only output TCMB's own TR/EN names; if any tool ever
  emits a translation, it must carry that notice.)
- Data used in another product, even commercially, must not be passed on to users as an extra
  charge.
- "EVDS uygulaması içeriği hiçbir şekilde yatırım tavsiyesi içermemektedir." — not investment advice.
- Usage may be logged and monitored.

evds3 has no real `robots.txt` (the path returns the SPA's `index.html`).

---

## 3. TCMB documents — keyless

All documents live under `https://www.tcmb.gov.tr/wps/wcm/connect/…` (IBM WCM). Same website
terms as §1. PDF links look like `/wps/wcm/connect/{uuid}/{name}.pdf?MOD=AJPERES&CACHEID=…`;
the `CACHEID` part can be dropped (verified for DUY2026-01.pdf and the report PDFs).

**Detail-page slugs are inconsistent** (`Inflation+Report+2026+-+III`, `Volume+42`, `Sayi+1`,
`Financial+Stability+Report-May+2012%2C+Volume+14`). URLs must be **discovered by following
links from index pages, never constructed**.

### 3.1 MPC (PPK) interest-rate decisions
- TR index: `https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/PPK/PPK+Toplanti+Kararlari`
  lists every decision since 2014 (`DUY2014-01` … `DUY2026-28`).
- **That index lags.** The September 2026 decision `DUY2026-38` is missing from it and its PPK path
  returns 404. It *is* at `…/TR/TCMB+TR/Main+Menu/Duyurular/Basin/2026/DUY2026-38`.
  → Discover from **both** the PPK index and the yearly press-release lists
  (`…/Duyurular/Basin/{YYYY}`), filtered by title "Faiz Oranlarına İlişkin Basın Duyurusu".
- EN: `…/EN/TCMB+EN/Main+Menu/Announcements/Press+Releases/{YYYY}/ANO{YYYY}-{NN}`, title
  "Press Release on Interest Rates (YYYY-NN)". **Numbering matches TR** (`DUY2026-23` ↔ `ANO2026-23`).
- Each decision is an HTML page **plus a PDF** (`DUY2026-01.pdf`, `ANO2026-01.pdf`). The PDF is
  clean, about 1–2 pages, and begins: "Para Politikası Kurulu Kararı / 22 Ocak 2026 / Sayı: 2026-01 /
  Toplantıya Katılan Kurul Üyeleri …". → **Use the PDF as the canonical text** (real page numbers).
  The HTML is mostly site navigation.
- Volume: about 8 decisions/year × 13 years × 2 languages ≈ 200 small PDFs.

### 3.2 Inflation Report (Enflasyon Raporu), quarterly
- EN index: `…/EN/TCMB+EN/Main+Menu/Publications/Reports/Inflation+Report` (2006-I … 2026-III).
- 2026-III page ("Inflation Report 2026 - III (August 13, 2026)") offers a full PDF
  (`Inflation+Report2026iii.pdf`, 65 pages, 2.5 MB) plus per-chapter PDFs.
- PyMuPDF: 65 pages in 0.18 s, about 196k chars of clean text. **No embedded TOC/bookmarks.** Printed
  page numbers differ from PDF page indices (PDF page 3 = printed "1"). The outline must be derived
  from the "Contents" page and heading fonts. **Citations must carry the PDF page and the printed
  page label.**
- Tables: most "tables" are charts. `find_tables()` on a chart page returned empty cells. The
  PyMuPDF vs Docling decision is deferred to a Phase 2 ADR after testing on real tables.

### 3.3 Financial Stability Report (Finansal İstikrar Raporu), semi-annual
- EN index: `…/EN/TCMB+EN/Main+Menu/Publications/Reports/Financial+Stability+Report` (Volume 1, 2005 … Volume 42, May 2026).
- Volume 42 offers `Full+Text.pdf` (81 pages, 2.5 MB) plus chapter PDFs. No embedded TOC; it has a
  dotted-leader Contents page.

### 3.4 Survey of Market Participants (Piyasa Katılımcıları Anketi), monthly
- EN page: `…/EN/TCMB+EN/Main+Menu/Statistics/Tendency+Surveys/Survey+of+Market+Participants`;
  TR: `…/TR/TCMB+TR/Main+Menu/Istatistikler/Egilim+Anketleri/Piyasa+Katilimcilari+Anketi/`.
- **Only the latest report is published** (`SMP_Report.pdf` / `PKA_Rapor.pdf`, 6 slide pages,
  mostly charts, about 5.7k chars). No archive of past monthly reports was found on either page.
  → We can only snapshot it from now on. Historical survey values are numeric series in EVDS
  (key required).

### 3.5 Other discovery aids
- Publications RSS: `https://tcmb.gov.tr/wps/wcm/connect/EN/TCMB+EN/Bottom+Menu/Other/RSS/Publications`
  (lists recent PDFs). This is a polite way to detect new releases.

---

## 4. International keyless source — **not included in v1**
- World Bank API (`api.worldbank.org/v2`) returned **HTTP 502** at check time. The WB general
  terms restrict API use "to facilitate commercial uses" and point to separate dataset terms
  that the fetched page did not show cleanly. *License for WDI not verified.*
- IMF moved to an SDMX 3.0 portal (data.imf.org). Key requirements and terms were *not verified*.
- DBnomics: not checked (it is an aggregator, so licences vary per upstream provider).

Under the rule "only if Phase 0 verifies clear terms and no key requirement", none qualifies
today. It is excluded from v1 and can be revisited later behind the same `DataSource` interface.

---

## 5. MCP Python SDK (verified by installing it)
- PyPI `mcp` **2.3.0** (2026-10-02) is current. **v2 is the stable line** (2.0.0 shipped
  2026-08). v1.x (`from mcp.server.fastmcp import FastMCP`) is maintenance only.
  Docs: https://py.sdk.modelcontextprotocol.io/ , migration: https://py.sdk.modelcontextprotocol.io/migration/
- **v2 renamed FastMCP → `MCPServer`.** Verified in a scratch venv:
  - `from mcp.server import MCPServer` (same object as `mcp.server.mcpserver.MCPServer`)
  - `from mcp.server.mcpserver import Context`; inject it as a parameter `ctx: Context`
    (`get_context()` is removed).
  - `from mcp.server.mcpserver.exceptions import ToolError`. A `ToolError` becomes an
    `is_error=True` result whose message reaches the model (verified).
  - `@mcp.tool(…, annotations=…, structured_output=…)`. A pydantic return type produces
    `output_schema` + `structured_content` (verified).
  - `@mcp.resource("doc://{doc_id}")` templates (RFC 6570, path-traversal rejection on by default);
    `@mcp.prompt()`. Both verified.
  - `MCPServer(name, instructions=…, lifespan=…)`, `mcp.run(transport="stdio")`.
  - Testing: `async with mcp.Client(server) as c:` connects **in memory** (verified).
  - Sync handlers run on worker threads in v2; use `async def` for I/O tools.
  - The SDK itself depends on `httpx2` (not `httpx`).
- The separate `fastmcp` package (PrefectHQ, now 4.x) is **not** the official SDK and will not be used.
