# SuriQ AI MVP implementation status

The local workflow is implemented from project creation through report export. The acceptance fixtures exercise all 12 numbered scenarios at API level, with the primary workflow also exercised in Chromium. The PRD pilot release gate still needs real supplier documents, a labeled accuracy evaluation, and procurement specialist sign-off.

| PRD coverage | Status |
| --- | --- |
| DOC-01–05 and FR-01 | One BOM and up to five quotations per batch, project and batch management, SHA-256 duplicate checks, explicit purchase scope, supplier coverage, local deletion, and original-file downloads. |
| FR-02–03 | Native PDF and XLSX extraction, local image-only OCR, immutable sources, editable fields, linked manual rows, page/sheet previews, and extraction retry. |
| FR-04–05 | Exact IDs and normalized material names first. Ambiguous candidates and packaging conversions are operator-controlled. Optional Ollama integration is implemented. Live Qwen inference remains unverified. |
| FR-06–09 | Rules cover material identity, dimensions, grade, standard, required brand, explicit specification, quantity, units, pack sizes, missing/additional items, duplicates, line/subtotal/payable arithmetic, VAT, freight/charges, validity and delivery. Unknown values remain reviewable or incomparable. |
| FR-10–12 | Root-cause issue tabs with every linked full PDF page or worksheet, exact source highlights, synchronized focus and zoom, severity, review state, and factual notes. |
| FR-13–14 | Four review states, internal notes, timestamped original/edited history, manual matching, evidence links, and recalculation after interpretation changes. |
| FR-15–16 | Comparison by material and supplier with cost basis and reason codes. PDF and CSV reports include terms, source references, review state, and conversion assumptions. |
| NFR-01–03, 05–07 | Loopback deployment, no hosted processing, bounded local intake, original preservation and retry, keyboard issue navigation, and decimal-safe financial comparisons. |
| NFR-04 and stage D | Synthetic text and scanned timing completed. Representative-device and real-world precision/recall measurements remain open. |

## Pilot limitations

- PDF table recovery works best with recognizable column labels and one material line per row. Wrapped descriptions, irregular layouts, handwriting, and mixed image/text pages can require manual staging. OCR is applied to pages without native words, and English is the default.
- XLSX exposes populated cells and saved formula results. It does not reproduce merged-cell layouts, charts, or original workbook styling, or recalculate formulas.
- Material identity and specifications are textual comparisons. Unstructured supplier wording, date formats, multi-line bundled pricing, and commercial terms without explicit labels can require operator interpretation. One confirmed mapping per quotation line is supported. Disputed or unclear amounts stay incomparable.
- Weak or missing evidence can block confirmation. A finding based on absence from an offer stays a reviewer task when there is no exact source location for the absence.
- The local deployment assumes one operator, with access controlled by the OS. Legal, security, commercial distribution, and procurement specialist reviews remain pilot prerequisites.
- The current delivery uses documented local commands. Linux was tested. Windows through WSL2 and optional Ollama inference were not exercised in this environment.
