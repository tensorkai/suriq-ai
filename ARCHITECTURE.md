# SuriQ AI architecture

The workspace verifies one BOM and one to five current supplier quotations for a user-selected procurement round. Next.js serves the local UI and proxies writes with an intent header. FastAPI owns intake, evidence, verification, and exports. SQLite stores original file blobs, immutable extraction snapshots, overlays, verification runs, and review history. Startup adds intake, processing, and revision metadata to existing databases.

## Intake

1. The browser submits mixed files through one dialog. The API checks the extension, signature, MIME, size, ZIP expansion, and SHA-256 digest before storing the original. Poppler and openpyxl enforce PDF and worksheet limits during processing.
2. Poppler supplies native word positions and page images. Tesseract supplies word rectangles and confidence on image-only pages. openpyxl reads workbook cells, merged header ranges, formulas, and cached values. DOCX paragraphs and tables are indexed by their order in the original XML. A local LibreOffice conversion supplies a separate paginated view for supported self-contained DOCX files.
3. The classifier checks requirements headings, estimated pricing, offer terms, and contradictory or unsupported document signals. It records the automatic role, a short explanation, and original evidence. It uses source content independently of the filename.
4. When enabled, Ollama or Gemini 3.1 Flash-Lite interprets selected pages or cell windows and receives rendered PDF page images where available. It returns a Pydantic-defined role proposal, cited source IDs, material fields, and terms. The service rejects unknown IDs, invalid schema, unrelated rows, ambiguous matches to source text, numeric fragments, and model-generated coordinates. It preserves stronger native fields on conflict and marks model-proposed associations for review.
5. The extraction snapshot records the parser, OCR and schema versions, model tag and digest when used, inference outcome, raw values, and source anchors. The API persists exception decisions, field edits, row exclusions, and match selections as audited changes. Role overrides reuse the role-neutral extraction and recompute verification. A revised quotation links to its predecessor without changing the original.

## Verification and evidence

The service enforces one accepted BOM, one to five current quotations, reviewable rows, and explicit purchase scope. The native PDF parser recognizes two-level material and labor headers, keeps each price component linked to its source, and groups schedules by printed issuer and site. Service rows remain visible for reference. When a BOM contains multiple schedules, purchase scope selects one schedule and matching uses that schedule's material rows. Match candidates come from exact IDs and normalized material attributes. The configured model can suggest existing candidate IDs for human confirmation. Decimal rules check quantities, packaging, specifications, line amounts, subtotal, VAT, other charges, delivery, and offer expiry. Missing costs remain unknown. Partial and unknown scope remain distinct from a documented full-scope omission.

Issue identity combines the batch context, BOM row or quote context, rule, and normalized conflicting condition. Each issue owns every affected source occurrence. The viewer partitions those occurrences into full PDF pages, read-only worksheets, or DOCX sections. DOCX section anchors are stable across different pagination. The paginated preview has no geometric overlay. Reviews attach to an issue and its evidence fingerprint. Changed evidence opens a fresh review while retaining prior decisions.

PDF and CSV reports use the current verified snapshot and include evidence locations, comparison caveats, document revisions, and correction history. A source update marks results stale while processing. Processing completion and automatic reverification share a database transaction.

## Operating boundaries

- The service and Ollama bind to loopback. Ollama requires installed model weights and runs locally after setup.
- Gemini mode uses a server-side key and a fixed Google HTTPS endpoint. Each upload, retry, or match request must opt in before it can send excerpts or images. Native extraction and verification stay local. The provider is recorded in the extraction snapshot.
- Jobs run with two extraction slots in a single FastAPI process. Startup marks interrupted processing for retry. Originals and earlier snapshots remain available.
- LibreOffice uses an isolated profile with macros disabled. The preview endpoint declines external references, embedded active content, and unsupported Word fields. The structured DOCX viewer remains available for such files.
- Formula values are read from the workbook cache. Excel formulas are not recalculated. Highly irregular tables, image regions alongside native PDF text, weak OCR, and uncertain cross-page row continuations can require manual review.
