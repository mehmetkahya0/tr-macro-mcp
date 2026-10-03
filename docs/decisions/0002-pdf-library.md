# 0002 — PDF text extraction: pypdfium2

Date: 2026-10-03 · Status: accepted

## Context
The brief suggested PyMuPDF (or Docling if tables matter). Checked on PyPI: **PyMuPDF is
AGPL-3.0 (or a commercial licence)**, which conflicts with this MIT project's terms for anyone
who redistributes it. Benchmark on real TCMB PDFs (MPC decision 2026-01 TR, Inflation Report
2026-III EN 65 pp., FSR May 2026 EN 81 pp., Survey of Market Participants Sept 2026 6 pp.), with text
similarity measured against PyMuPDF output over the first 20k normalised characters:

| Library (licence) | IR time | FSR time | Similarity IR / FSR / SMP | Turkish chars |
|---|---|---|---|---|
| PyMuPDF 1.27 (AGPL) | 0.16 s | 0.21 s | 1.000 (reference) | ok |
| **pypdfium2 5.13 (BSD-3/Apache-2.0)** | 0.22 s | 0.26 s | 0.999 / 1.000 / 1.000 | ok, identical counts |
| pypdf 6.x (BSD-3) | 3.2 s | 4.2 s | 1.000 / 0.999 / 1.000 | ok |
| pdfplumber 0.11 (MIT) | 7.6 s | 9.8 s | 1.000 / 0.909 / 0.472 | ok |

No library recovered useful tables from IR/FSR "tables": they are mostly charts (images or
vector graphics with scattered labels). Docling (MIT) does layout/table models but pulls in
PyTorch and model downloads, which goes against the "base install stays light" goal.

## Decision
Use **pypdfium2** for text, page access and bookmarks. Printed page labels and the outline are
derived heuristically (running-header page numbers; dotted-leader Contents page), because TCMB
report PDFs carry no bookmarks or page-label dictionaries (verified).

## Consequences
- +: permissive licence, PyMuPDF-level speed and fidelity, one small binary wheel.
- −: no table extraction. Chart/table numbers are not reliably available as text; tools return
  the page text and citation, and the README lists this as a limitation.
- pdfium marks line-break hyphens as U+FFFE; we map it to "-".
- Revisit with Docling as an optional extra if the eval shows table questions failing.
