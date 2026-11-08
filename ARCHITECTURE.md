# SuriQ AI architecture

The MVP follows the supplied PRD for one-time procurement. The supporting project document informs the workflow and the separation between project-wide demand and the current purchase. The operator establishes which BOM rows are in scope and how much coverage each supplier claims.

## Components

| Component | Responsibility |
| --- | --- |
| Next.js App Router | Local React workspace, staging, review, source navigation, comparison, and exports |
| Next.js route handler | Same-origin API entry point with loopback host checks, body bounds, and write-request intent header |
| FastAPI | Transactional project, batch, document, correction, verification, and report API |
| Poppler and Tesseract | Native PDF word geometry, full-page PNG rendering, OCR word rectangles, and confidence for image-only pages |
| openpyxl | Worksheet coordinates, original cell display, and saved formula results |
| SQLite | Projects, batches, original BLOBs and hashes, extraction snapshots, correction overlays, verification runs, review state, and audit events |
| Optional Ollama | On-demand suggestions from a bounded set of existing candidate identifiers |
| ReportLab and CSV | Local verification exports |

The implementation uses Poppler raster pages as the PDF.js equivalent. A page can be downloaded in its original document. XLSX renders a value/formula grid with stable cell identifiers. Both viewers are read-only. Native HTML controls and a modal dialog provide keyboard interaction. The interface uses Tailwind, CSS and local Lucide icons. Python stores originals in SQLite BLOBs to make file import and database state atomic. Native tools receive private temporary copies for processing and rendering.

## Data flow

1. Intake validates role, extension, signature, size, hash, file count, and bounded workbook expansion. The original is persisted before extraction starts.
2. Extraction records page or sheet identifiers, material lines, commercial terms, original text and word rectangles or cell addresses. OCR uncertainty and workbook errors are exposed with source locations. The service keeps immutable extraction snapshots, including successful retries.
3. The operator corrects interpretations as overlays. Source anchors can be retained or replaced with a selected cell or marked PDF region. Changes are recorded with prior values and timestamps.
4. Verification uses exact identifiers, normalized material names and corroborating specifications. Ambiguous candidates require operator mapping. One quote line can have several candidates and one confirmed BOM match. The optional model can only propose IDs that were sent in the request.
5. The rules compare scoped requirements, validate source-linked line arithmetic, and check commercial terms. Results are persisted with scope, coverage, extraction snapshot IDs, and the audit position at which the run was made.
6. Each finding groups evidence by batch, BOM row or quote context, rule, and conflicting values. The UI partitions the evidence inside that tab by document and source location. Review decisions apply to an evidence fingerprint and reopen when the finding or evidence changes.

## Operating assumptions

- One local workstation account operates the service. The application and worker bind to loopback. The database is protected by local filesystem permissions. Use a protected workspace and OS-level storage encryption when the records require it.
- Both quantities and monetary calculations use decimal arithmetic. PHP is the display default unless the quote states another currency. Foreign currency amounts are displayed without conversion.
- Unit aliases cover common Philippine quotation abbreviations. Metric conversions use fixed factors. Packaging equivalence needs compatible documented pack sizes or an operator-supplied factor with a reason.
- VAT inclusivity, documented rates and adjustments remain explicit. A ranked total requires a documented, internally consistent cost basis and aligned scope, specification, quantity, and currency.
- Expiry and required delivery comparisons accept supported literal dates and explicitly described time bases. A quote is assessed against the Manila calendar at verification time.
- Extraction is bounded and format-dependent. At most two documents extract concurrently. Interrupted work is marked for retry when the service starts. The single worker deployment avoids conflicting job ownership.
- Poppler and Tesseract are system dependencies. Evaluate the licenses of all components and any intended packaging before commercial redistribution. Native binaries and the model are installed separately.
