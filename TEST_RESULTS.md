# SuriQ AI test results

Executed on 7 November 2026 in Fedora Linux 44, on an AMD Ryzen 5 5625U host with 12 logical CPUs and 22 GiB of visible RAM. The environment used Node.js 24.18.0, Python 3.14.8, Poppler 26.01.0, Tesseract 5.5.4, and Chromium 156. Ollama was disabled during acceptance and browser testing.

## Release checklist

The API suite generates the original PDFs and spreadsheets and exercises the same intake and verification code as the application. These results apply to the generated fixtures.

| Test | Result | Evidence |
| --- | --- | --- |
| AT-01 | Pass | One BOM and three assigned multi-page supplier PDFs imported and extracted. |
| AT-02 | Pass | One dimension issue links BOM page 2, Supplier A page 3, and Supplier B page 1. Chromium displayed all three full pages with original-text overlays. |
| AT-03 | Pass | Quantity discrepancy appears as a separate keyboard-navigable tab. |
| AT-04 | Pass | Ten of 100 BOM items selected. The other 90 caused no missing-material findings. |
| AT-05 | Pass | A partial offer lacking a selected item displays “not offered.” |
| AT-06 | Pass | XLSX evidence points to B2 and OCR evidence has PDF rectangles. A stricter confidence threshold exercised the weak-OCR source review state. |
| AT-07 | Pass | Documented 40 kg and 50 kg pack sizes are flagged separately from an unknown pack size. |
| AT-08 | Pass | Explicit inclusive and exclusive VAT yield distinct payable totals. Missing freight blocks comparability. Ambiguous and contradictory tax declarations stay unknown. |
| AT-09 | Pass | Wrong subtotal and absent required grade generate source-linked findings. |
| AT-10 | Pass | A correction removes one supplier's evidence from the dimension issue. The remaining issue can be dismissed, and the correction and review are audited. Chromium verified persistence after reload. |
| AT-11 | Pass | A blank scanned page remains viewable and is flagged. Confirmation is rejected when required evidence is absent. |
| AT-12 | Pass | A partial offer with unknown freight stays incomparable. Only documented comparable totals are ranked. |

Additional regression checks cover file signature and duplicate rejection, original persistence and deletion, interrupted extraction retry, oversized sparse workbooks, PDF rotation, and large decimal values. The browser workflow also checked synchronized focus, PDF/CSV downloads, cross-origin rejection, the write-intent check, and a 390 px viewport.

## Commands

The API and regression suite passed all 13 tests in 4.93 s. ESLint, Next route type generation, TypeScript, the production build, dependency consistency, and the production Chromium workflow also passed.

From the repository root after installing `requirements-dev.txt` and system OCR dependencies:

```sh
.venv/bin/python -m pytest -q
npm run lint
npm run typecheck
npm run build
.venv/bin/python -m pip check
```

For the browser test, start both services with an isolated `SURIQ_DB`. Install the test browser with `.venv/bin/python -m playwright install chromium`, then run:

```sh
SURIQ_TEST_URL=http://127.0.0.1:3000 .venv/bin/python -m tests.browser_workflow
```

The executed production run used port 3100 and an existing Chromium executable selected via `SURIQ_CHROMIUM`. The browser test creates synthetic data in the configured workspace.

## Synthetic throughput

`.venv/bin/python -m tests.benchmark` generated one BOM and three 10-page quotations, with one material row per page. Both runs checked all 40 pages and rows and one dimension issue with three linked sources. Timings include API intake, extraction, scope update, and verification. Fixture creation is excluded.

| Format | Upload to findings |
| --- | ---: |
| Native text PDFs | 0.40 s |
| Scanned PDFs, all 40 pages OCR'd | 25.11 s |

The PRD accuracy and performance release gates still need a human-labeled supplier corpus and benchmarking on its reference device. Live Qwen inference, Windows/WSL2, additional browser engines, and commercial distribution review were outside this verification run.
