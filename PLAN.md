# tr-macro-mcp — Plan

Status: Phase 0 approved and merged (2026-10-03). **Phase 1 implemented on `phase-1`,
awaiting review.** Open item: EVDS parser validation against recorded live responses (needs a key).
Facts are in `docs/data-sources.md`; competitor analysis is in `docs/positioning.md`.

Defaults applied when Phase 0 questions went unanswered: uv installed via `pip --user`; repo stays
local (no GitHub remote, nothing pushed); D4/D6 accepted; Phase 2 corpus as proposed below.

## 1. Proposed deviations from the brief

| # | Brief says | Proposal | Why |
|---|---|---|---|
| D1 | "official Python SDK (FastMCP)" | Use official `mcp` **v2** (`mcp>=2.3,<3`) and its `MCPServer` class | v2 is the stable line; FastMCP was renamed to `MCPServer` in v2 (verified by install). v1 is maintenance only. |
| D2 | Docs fetched as PDFs | MPC decisions: use the **PDF** attached to each press release as canonical text; HTML is only for discovery | PDF gives real page numbers; the HTML is mostly site chrome |
| D3 | MPC docs from one index | Discover from the **PPK index ∪ yearly press-release lists** (title filter) | The PPK index lags (Sept 2026 decision missing) |
| D4 | Survey of Market Participants as a document corpus | Ingest **only the latest** SMP PDF and keep monthly snapshots from now on. Historical SMP values are EVDS series (key) | TCMB publishes only the latest report; no archive found |
| D5 | `search_series` | Local catalog index (categories → datagroups → serieList) cached in DuckDB, built on first use with the user's key, TTL 7 days | EVDS has no keyword-search endpoint |
| D6 | Optional international source | **Exclude from v1** | No candidate passed "clear terms + keyless" verification today |
| D7 | Citation `page` | `page` = 1-based PDF page **and** `page_label` = printed page number when detectable | They differ in IR/FSR (PDF p.3 = printed "1") |
| D8 | Outline extraction | Heuristic: Contents page (dotted leaders) + heading font sizes; embedded TOC when present | IR/FSR PDFs have no bookmarks |
| D9 | FX history | Support 1996-06 onward; pre-2005 values are returned in TRY with an explicit `redenominated_from_TRL: true` flag and the raw TRL value | 2005 redenomination; ISO-8859-9 files |
| D10 | — | The keyless eval share will lean on FX + MPC + IR + FSR, since SMP has one document | Follows from D4 |

## 2. Architecture (target)

```
MCP client ──stdio──> server.py (MCPServer, instructions incl. disclaimer)
                        ├─ tools/*  (thin: validate input → call service → build output + citations)
                        ├─ datasources/registry ─┬─ TcmbXmlFxSource (keyless)
                        │                        └─ EvdsSource (EVDS_API_KEY)
                        ├─ store/timeseries (DuckDB: observations, catalog, fetch log w/ TTL + as_of)
                        ├─ docs/ (registry, fetch, parse, navigate) ── Mode A
                        ├─ rag/  (chunk, embed, index, retrieve, rerank) ── Mode B, [rag] extra
                        └─ security/sanitize (excerpt wrapping, instruction neutralisation)
```

Key models (pydantic v2, in `models.py`):
- `Citation{source, title, url, doc_date, page, page_label, bulletin_no, series_code, retrieved_at}`
- `Observation{date, value, unit}`, `SeriesResult{series_code, name_tr, name_en, frequency, unit, observations, citations, notes}`
- `FxRateResult{status: "ok"|"not_published"|"not_found", requested_date, bulletin_date, currency, unit, forex_buying, forex_selling, banknote_buying, banknote_selling, nearest_earlier_bulletin?, citations, notes}`
- `Excerpt{doc_id, page, page_label, text}`: text is always wrapped as untrusted data.

`DataSource` protocol (the one deliberate abstraction):
`name`, `coverage`, `requires_key`, `available() -> bool`, `get_series(code, start, end, frequency?, aggregation?) -> SeriesResult`.
FX is a source like any other, exposed via series codes such as `FX:USD:forex_selling`, so that
`get_series`/`compare_periods`/`explain_move` work uniformly in keyless mode.

Caching / politeness: one shared `httpx.AsyncClient` with a descriptive UA, ≥1 s spacing per host,
retries with backoff on 5xx/429, conditional GET (ETag) where offered. Cache under
`%LOCALAPPDATA%`/XDG cache dir (configurable `TR_MACRO_CACHE_DIR`). Historical FX bulletins never
expire. `today.xml` and EVDS observations expire after 24 h; catalog after 7 d.

## 3. Phases

### Phase 1 — Foundation + numeric tools (branch `phase-1`)
1. Scaffold: `pyproject.toml` (uv; extras `rag`, `eval`, `dev`), ruff, mypy (strict on `src/`),
   pytest, `.env.example`, MIT LICENSE, GitHub Actions (ruff, mypy, pytest, no network).
2. `config.py` (pydantic-settings, `.env`), `models.py`, shared HTTP client.
3. `TcmbXmlFxSource` + fixtures: today.xml, a normal day, a weekend 404, a holiday 404, a
   pre-2005 ISO-8859-9 file, a JPY Unit=100 case.
4. `EvdsSource` + DuckDB store, with 1000-observation chunking. **Fixtures recorded with your key**,
   key header stripped from recordings.
5. Tools: `list_data_sources`, `get_fx_rate`, `search_series`, `get_series`, `compare_periods`.
   EVDS-only tools return `EVDS_API_KEY not set; see README` with no key.
6. Tests: in-memory `Client(server)` integration tests + unit tests. README with Claude Desktop /
   Claude Code config for keyless and key modes.

### Phase 2 — Documents + Mode A (branch `phase-2`)
Discovery from index pages + RSS, polite fetch, SHA-256 checksums, idempotent registry
(DuckDB table), PyMuPDF parsing with page labels, heuristic outlines, `list_documents`,
`get_document_outline`, `read_document`, `grep_documents`, `security/sanitize.py`,
a synthetic poisoned-PDF fixture + test, and an ADR on PyMuPDF vs Docling.
Default corpus (see Q3): MPC 2014→now TR+EN, IR 2022→now EN+TR, FSR 2022→now EN+TR, SMP latest.

### Phase 3 — Cross-source tools (branch `phase-3`)
`compare_ppk_statements` (sentence alignment + difflib; heuristic hawkish/dovish lexicon clearly
labelled `heuristic: true`), `explain_move` (series/FX movement + excerpts from documents dated in
the window, no causal claims), resources `tcmb://documents/{doc_id}` and
`tcmb://documents/{doc_id}/pages/{page}`, 3 prompts, 3 scripted demos (2 keyless).

### Phase 4 — Eval harness + Mode A baseline (branch `phase-4`)
`eval/dataset/questions.jsonl` with 60+ items drafted from real documents/series, each with
`requires_key`, `category`, `lang`, `source_ref`, `verified: false`, and ≥40% keyless. Pluggable agent
runner (Groq / Gemini / Anthropic via env; their free tiers are verified at that time, not now),
metrics, versioned judge prompt, `make eval-quick` (offline, keyless, no LLM), reports.

### Phase 5 — Mode B + comparison (branch `phase-5`)
`[rag]` extra, ADRs (vector store; embedding model BGE-M3 vs multilingual-e5), `search_docs`,
ablations (fixed vs section-aware chunking, embedding model, hybrid vs dense, ± reranker),
recall@k/MRR, full comparison, `docs/eval-results.md` with an honest recommendation.

### Phase 6 — Polish
README (Mermaid diagram, quickstart, tool reference, eval summary, disclaimer, limitations, TCMB
terms notice), demo script, fresh-clone test in under 5 minutes.

## 4. Risks
- **TCMB site structure changes** (WCM slugs are irregular). Mitigation: discovery by link-following +
  title filters, fixtures of index pages, and a clear "not found" result.
- **EVDS response shape unknown until recorded.** Mitigation: record first, then code.
- **Commercial-use clause.** TCMB content needs written permission for commercial use. The project is
  non-commercial OSS and the README will say so. Only small TCMB files go in the repo as test
  fixtures (attributed); the corpus is downloaded by the user at runtime.
- **SMP corpus is one document**, so SMP document questions will be few.
- **Embeddings on CPU** (BGE-M3 is ~2.2 GB). Mitigation: the `[rag]` extra is optional, with cached indexes.

## 5. Open questions (see chat)
Q1 uv install · Q2 EVDS key for fixtures · Q3 corpus window · Q4 GitHub remote · Q5 confirm D6/D4.
