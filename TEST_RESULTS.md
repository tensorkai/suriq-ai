# SuriQ AI v2 test results

Executed on 8 November 2026 on Fedora Linux 44, AMD Ryzen 5 5625U, 12 logical CPUs, and 22 GiB RAM. The test stack used Python 3.14, Node.js 24, Poppler 26.01, Tesseract 5.5.4, LibreOffice Writer, and Chromium. All documents in this run were synthetic fixtures.

## PRD v2 acceptance checklist

| Test | Result | Observed behavior |
| --- | --- | --- |
| AT-01 | Pass | One browser action uploaded a three-page BOM and three multi-page quotations without role selection. All four were identified from content. |
| AT-02 | Pass | A BOM named `quote.pdf` and a quotation named `BOM.xlsx` received the correct roles. |
| AT-03 | Pass | A title-free BOM with reordered columns, merged headings, and two worksheets mapped quantity evidence to each original C3 cell. |
| AT-04 | Pass | A PDF with a letterhead, continued table, and footnote terms produced two anchored rows and page-two validity evidence. |
| AT-05 | Pass with review | Actual Tesseract OCR processed a scan with irregular alignment. Intake left unsupported fields unresolved and the original page viewable. A separate scan produced precise OCR boxes. |
| AT-06 | Pass | A DOCX quotation provided terms from paragraphs, line evidence from its original table cell, and a locally rendered PDF preview. |
| AT-07 | Pass | Two different BOMs blocked verification with a multiple-BOM message and preserved both originals. |
| AT-08 | Pass | A blank workbook and invoice produced unknown and unsupported states respectively. Quotations did not become the baseline. |
| AT-09 | Pass with review | A combined requirements-and-offer packet stayed mixed until an audited role correction. The reviewer could exclude reference rows and retain the original. |
| AT-10 | Pass | Simulated Ollama unavailability, malformed JSON, nonexistent element IDs, and fabricated values produced review states and retained retry/manual paths. |
| AT-11 | Pass | An exceptional BOM-to-quotation override preserved the original decision and its reason, revalidated the batch, and included the corrected offer in comparison. |
| AT-12 | Pass | Chromium showed one dimension issue with three original PDF page cards and six source-word rectangles. |
| AT-13 | Pass | The independent quantity discrepancy appeared in a separate keyboard-navigable tab. |
| AT-14 | Pass | Separate groups were created for 12 mm and 10 mm offers against 16 mm requirements. |
| AT-15 | Pass | Selecting 10 of 100 BOM rows produced no missing findings for the other 90. |
| AT-16 | Pass | A partial offer was shown as not offered for an omitted item. An unknown packaging factor was left for clarification. |
| AT-17 | Pass | A one-cent line error was recomputed with Decimal and linked to quantity, price, and amount cells. Ambiguous VAT left the offer incomparable. |
| AT-18 | Pass | A simulated model-supplied box was rejected by schema validation. Accepted model values used parser-owned coordinates and remained reviewable. |
| AT-19 | Pass | A scan reading was linked to its visible region, manually matched, and reverified. The reviewer decision and correction history persisted. |
| AT-20 | Pass | Partial coverage and undocumented freight prevented comparable totals. |
| AT-21 | Pass on synthetic fixtures | The production browser workflow and an installed Qwen3-VL intake smoke test both ran in network namespaces with only loopback. The model-assisted document remained reviewable and the viewer and CSV export worked offline. |
| AT-22 | Pass | Invalid signatures, legacy formats, and duplicate hashes were rejected. A corrupt PDF retained its original and recoverable status. A revision preserved the original and only the current offer participated in comparison. |

## Commands and scope

- `.venv/bin/python -m pytest -q` passed all 28 acceptance and regression tests with Tesseract and LibreOffice available. Model error cases use deliberately controlled responses. Native PDF, OCR, XLSX, DOCX, SQLite, and reports ran through the actual local API.
- `npm run lint`, `npm run typecheck`, `npm run build`, `.venv/bin/python -m pip check`, and `git diff --check` passed.
- `.venv/bin/python -m tests.browser_workflow` passed against the production Next.js build with a separate database. It checked unlabeled mixed upload, grouped evidence, keyboard focus, correction, review persistence, comparison, downloads, request restrictions, and a 390 px viewport.
- `.venv/bin/python -m tests.live_model` passed with Ollama 0.40.15 and Qwen3-VL 4B Instruct on CPU in 93.0 s. The model read an unfamiliar PDF quotation and supplied source-linked material, quantity, unit price, and line amount. It omitted a standalone unit field, so the line remained flagged and the offer remained incomparable. Earlier runs either timed out or returned unusable citations and correctly retained the original for review.

## Synthetic throughput

`.venv/bin/python -m tests.benchmark` processed four 10-page documents in a loopback-only namespace with model assistance disabled. Both runs recovered 40 material rows and one dimension issue across three sources. Measured upload-to-verification time was 0.27 s for native PDFs and 18.18 s for scanned PDFs. Fixture generation was excluded.

## Open release gates

A single synthetic run cannot establish the PRD's real-world macro-F1, field precision and recall, or evidence accuracy targets. The labeled supplier corpus, adjudication, reference-device AI benchmark, and pilot sign-off remain open.

## Gemini provider

The full API suite passed 31 tests. The Gemini tests intercepted the HTTPS transport to verify image and JSON-schema requests, opt-in on retry, parser-owned anchors, rate-limit handling, and local enforcement of output bounds. The production Chromium workflow passed with Gemini selected and native extraction. The authenticated synthetic Gemini smoke test passed in 1.9 s and recovered the item code, material, quantity, unit, unit price, and amount with source evidence. It also verified guarded comparison, PDF preview, and CSV export.

## Summary-only PDF quotations

The full API regression suite passed 32 tests. The added synthetic case covers centered headers, right-aligned prices, a draft quotation with only summary amounts, and suppression of item findings and partial-scope comparisons. The supplied PDF pair was reprocessed locally in the running app. All 36 BOM rows summed to PHP 386,938.68, and the quotation stated PHP 387,337.68 for materials. Chromium displayed the PHP 399.00 finding with highlights on BOM page 2 and quotation page 1. CSV export passed. Item coverage remained unverified.

## Priced BOM/BOQ schedules

The native PDF parser recognized the two-level material and labor headers in both supplied BOQs. The six-page file retained three separate supplier/site schedules with 42 material and 15 service rows. The two-page file retained 14 material and 5 service rows. All four material extension sums matched the printed amounts. Production Chromium showed the schedule summaries and separate price columns. The existing batch correctly requires one baseline BOM, and the synthetic API regression verifies that selected scope and item matching stay inside one schedule.

## Workspace loading states

Chromium held workspace, upload, verification, page preview, and correction requests open long enough to verify that their loading indicators appeared and cleared. The same workflow checked the unchecked sharing control, grouped source highlights, keyboard navigation, review persistence, exports, activity labels, and a 390 px viewport. The API suite passed all 33 tests.
