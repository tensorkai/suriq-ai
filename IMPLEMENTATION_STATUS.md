# SuriQ AI MVP v2 implementation status

The attached PRD v2.0 governs the local workflow. A later request added opt-in Gemini 3.1 Flash-Lite for non-confidential test documents. Hosted inference is an explicit exception to the original local-only requirement. The starting checkout already contained project and batch storage, native PDF/XLSX extraction, OCR, deterministic verification, issue tabs, reviews, comparison, and reports. Uncommitted work added a first pass at automatic upload classification and structured DOCX parsing. The upgrade extended those paths and kept the same Next.js, FastAPI, and SQLite deployment.

| Requirement | Status |
| --- | --- |
| FR-01 Unlabeled batch upload | One mixed-file chooser or drop target. The UI submits each file independently so one failure leaves other uploads intact. |
| FR-02 Input validation | Signature, MIME, duplicate hash, file size, Office archive, page count, and worksheet limits. Corrupt content is rejected or retained with a recoverable processing status. |
| FR-03 Automatic role identification | Content-based requirements and offer signals with persisted evidence. Bounded local interpretations can supply corroborating source context. Real-world classifier metrics are pending. |
| FR-04 Ambiguity handling | Unknown, mixed, and unsupported statuses block verification. Exceptional overrides require a reason and can exclude reference rows. |
| FR-05 Batch validation | Exactly one BOM and one to five current quotations with valid purchase scope and reviewable rows. Supplier identification is extracted where present and can be corrected. |
| FR-06 Layout-aware extraction | Native PDF, OCR, XLSX, and DOCX with literal anchors. Repeated table headers, same-layout page continuations, common aliases, merged XLSX header ranges, and limited wrapped descriptions. More difficult cross-page or split-field associations can require review. |
| FR-07 Vision-language assistance | Installed Qwen3-VL via loopback Ollama, with schema checks, parser-owned anchors, timeouts, and bounded retry. The offline CPU smoke test recovered material, quantity, price, and amount from an unfamiliar PDF. Proposed associations and the missing unit remained under review. |
| FR-08 Canonical preview | Editable line and term values, original text, linked source views, and audited overlays. Contradictory links are flagged. |
| FR-09 Scope selection | All or selected BOM rows and full, partial, or unknown supplier coverage. |
| FR-10 Semantic matching | Exact IDs, normalized attributes, and optional AI candidate suggestions. Ambiguity remains a human decision. |
| FR-11 Unit normalization | Fixed metric conversions and explicitly documented packaging or reviewer conversion factors. |
| FR-12 Discrepancy rules | Existing deterministic rules and evidence integrity checks retained, with incomplete material rows flagged. |
| FR-13 Financial arithmetic | Decimal, PHP and currency context, explicit VAT treatment, and conservative handling of unknown costs. |
| FR-14 Issue grouping | One issue per normalized condition, with separate groups for different values and separate rules on one item. |
| FR-15 Evidence viewer | Full PDF pages and read-only worksheet grids with highlights. DOCX has structured references and a separate local paginated preview for supported files. Geometry is unavailable for DOCX. |
| FR-16 Issue narrative | Deterministic notes and source values. Short notes are a target and are not universally capped at 25 words. |
| FR-17 Human review | Four review statuses, evidence fingerprints, notes, and an audit trail. Source corrections rerun rules and reopen changed findings. |
| FR-18 Supplier comparison | Coverage, specification outcomes, terms, and comparability reasons. Incomparable offers remain unranked. |
| FR-19 Reports | PDF and CSV with current findings, evidence locations, review state, correction details, and comparison caveats. |
| FR-20 Local resilience | Native fallback, retry, persisted original/snapshots, and no hosted inference. |
| FR-21 Provenance | Model tag and digest, prompt/schema and parser versions, relevant parser/OCR runtime version, and inference outcome recorded per extraction. |
| FR-22 Revisions | Explicit revision link, deduplication across revisions, preserved originals, and superseded status. |
| NFR-01–06 | Loopback processing, bounded file validation, evidence controls, keyboard issue navigation, decimal arithmetic, and retry. Local deployment assumes one operator with OS-level access control. |
| NFR-07–09 | Held-out role and extraction accuracy, source accuracy on real documents, and reference-machine VLM benchmarking remain open. |

## Release gaps

- The available document fixtures are synthetic. A labeled supplier corpus, procurement reviewer sign-off, and held-out per-format metrics remain required.
- The implementation uses the existing Poppler, Tesseract, and openpyxl parsers. Docling and PDF.js were listed as PRD candidates. Poppler provides the PDF rendering and geometric text layer for the current viewer.
- General cross-page row reconstruction, mixed image/text page OCR, large irregular tables, complex DOCX rendering, and cached workbook formula accuracy require broader evaluation. These cases can require source correction or remain incomplete.
- Distribution licensing, field testing on SME workstations, and accessibility testing beyond the exercised browser interactions remain open.

See [test results](TEST_RESULTS.md) for executed scenarios and outcomes.
