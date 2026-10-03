# Positioning

Checked 2026-10-03 by reading each project's README/tool list (read-only). Tool counts are as
documented there.

## Existing Turkish finance / TCMB MCP servers

| Project | Scope | TCMB data | TCMB documents | Citations | Key needed | Transport |
|---|---|---|---|---|---|---|
| [saidsurucu/borsa-mcp](https://github.com/saidsurucu/borsa-mcp) | 28 tools: BIST/US stocks, funds, crypto, FX (doviz.com), economic calendar, KAP news | EVDS (`get_evds_data`, `get_macro_data`), CPI/PPI | No | No structured citations | EVDS key for data | Remote HTTP |
| [tufankoc/finans-mcp](https://github.com/tufankoc/finans-mcp) | 19 tools: broker reports, consensus, portfolio optimisation, Monte Carlo, KAP, BDDK, TEFAS | EVDS (`tcmb_evds_gostergeler`) | No | No | Varies | stdio |
| [ofurkanuygur/tcmb_mcp](https://github.com/ofurkanuygur/tcmb_mcp) | 6 FX tools: current/historical rates, conversion, history stats | TCMB XML FX | No | No | None | stdio |
| [parttimegod/evds-mcp](https://github.com/parttimegod/evds-mcp) | 5 tools: search/get/summarise series, stationarity, cointegration | EVDS | No | No | EVDS key | stdio |
| [ahmthamza/turkiye-veri-mcp](https://glama.ai/mcp/servers/ahmthamza/turkiye-veri-mcp) | 16 tools: TÜİK, EVDS, BDDK, HMB | EVDS (+ local search index) | No | No | EVDS key for EVDS | Remote HTTP |

## What we do not duplicate
- **Market prices / stocks / funds / crypto**: borsa-mcp and finans-mcp cover these. Out of scope for us.
- **Portfolio analytics, simulations, investment-flavoured tools**: out of scope. We never give advice.
- **Generic EVDS access with advanced statistics** (stationarity, cointegration): evds-mcp does
  this well. Our EVDS tools stay thin (search, get, compare) and exist mainly to feed the
  cross-source tools.
- **TÜİK/BDDK/HMB**: turkiye-veri-mcp covers them.

## What is different here
1. **Documents + numbers together.** None of the servers above reads MPC decisions, Inflation
   Reports, Financial Stability Reports or the Survey of Market Participants. We do (navigation
   tools in Mode A, hybrid search in Mode B), and we combine them with series
   (`explain_move`, `compare_ppk_statements`).
2. **Citations on every result**: source, URL, document date, PDF page + printed page label,
   bulletin number for FX, `retrieved_at`. A "not found" result is explicit, never a guess.
3. **Transparent non-publication handling for FX.** tcmb_mcp documents an *automatic fallback to
   the previous business day*. We return "no bulletin for date X" and expose the nearest earlier
   bulletin as a separate, labelled field, so the date is never silently swapped. We also handle
   pre-2005 TRL bulletins explicitly.
4. **Useful with no key.** FX + all documents + the MPC diff work keyless. EVDS is an upgrade.
5. **Measured retrieval.** A published eval comparing agentic navigation (Mode A) against
   hybrid RAG (Mode B) on accuracy, citation correctness, tool calls, latency and tokens, with
   negative results reported as they are.
6. **Prompt-injection hygiene** for document text: delimited, labelled excerpts, plus a
   poisoned-PDF test.
