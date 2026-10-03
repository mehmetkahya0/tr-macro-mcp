# 0001 — HTTP client: httpx, not httpx2

Date: 2026-10-03 · Status: accepted

## Context
The brief specifies httpx. The official MCP SDK v2 (`mcp` 2.3.0) depends on `httpx2`, pydantic's
successor fork of httpx, so both libraries end up installed.

## Decision
Use `httpx` (0.28.x) for our own requests, wrapped in `PoliteClient` (per-host spacing, retries
with exponential backoff on 429/5xx and transport errors, one descriptive User-Agent).

## Consequences
- +: matches the brief; mature, well-known API; `httpx.MockTransport` gives fully offline tests
  without another dependency.
- −: two HTTP stacks in the environment (a few MB). Migrating later is mechanical, since only
  `httpclient.py`, the tests' transports and the recording scripts touch httpx.
- Revisit if httpx stops receiving releases or the SDK exposes a shared client.
