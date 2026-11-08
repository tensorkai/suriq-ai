# SuriQ AI

SuriQ AI is a local procurement verification workspace for Philippine construction teams. A batch links one bill of materials to up to five supplier quotations. It identifies roles from document contents and shows each distinct discrepancy in one issue tab with linked PDF pages, spreadsheet cells, or Word source sections.

## Install and run

Use a Bash-compatible shell on Linux or Windows with WSL2. Install Node.js 20.9+ and Python 3.12+. The implementation was verified with Node.js 24 and Python 3.14.

Install Poppler and Tesseract with English language data. Install LibreOffice Writer for paginated DOCX previews. On Ubuntu or Debian:

```sh
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-eng libreoffice-writer python3-venv
```

On Fedora:

```sh
sudo dnf install poppler-utils tesseract tesseract-langpack-eng libreoffice-writer
```

From the repository root:

```sh
npm ci
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Start the processing service in one terminal and the web app in another:

```sh
.venv/bin/python -m uvicorn service.api:app --factory --host 127.0.0.1 --port 8765
```

```sh
npm run dev
```

Open [the local workspace](http://127.0.0.1:3000). For the production build, keep the Python service running and use `npm run build` followed by `npm run start`. Package and model installation require network access. The deterministic workflow runs locally after installation.

## Review a batch

1. Create a project and procurement batch.
2. Choose or drop the BOM and supplier offers together. SuriQ accepts PDF, XLSX, and DOCX. Review the detected role and its source evidence. Use **Correct type** for an exception. For a mixed packet, exclude reference rows before using the rest as an offer.
3. Inspect flagged **Material lines**, **Document terms**, and **Original source** entries. To confirm a PDF reading, draw a box and transcribe its source text. For XLSX or DOCX, select the original cell or paragraph. Document the reason when excluding a row or supplying a packaging conversion.
4. Use **Set scope** on the BOM to select all or specific rows. Coverage defaults to unknown for supplier offers.
5. Run verification. Review the grouped source cards in **Findings** and save a decision. Use **Focus highlights** to center the relevant regions across the linked documents.
6. Review the supplier matrix, including reasons for incomparable totals, then download PDF or CSV from **Export**.

Use **Upload revision** on a current quotation when a supplier sends an updated offer. The old file remains in the batch as a superseded source once the revision is accepted. If extraction fails or the service restarts during processing, use **Retry extraction**. Check the original when extraction returns few rows or flags OCR. The SQLite database is created at `.suriq/workspace.sqlite` and contains the originals, results, and audit history. Stop the service before copying the database as a backup. Treat backups and downloaded reports as procurement records.

## Gemini 3.1 Flash-Lite

Create an API key for a **Free Tier** project in [Google AI Studio](https://aistudio.google.com/api-keys). Store it in the Git-ignored `.env` as `GEMINI_API_KEY`. The following command creates a private `.env` from the repository root and keeps the key hidden while you enter it. If the file already exists, edit the `GEMINI_API_KEY` assignment in that file.

```bash
(
  umask 077
  set -o noclobber
  read -rsp 'Gemini API key: ' suriq_key
  printf '\n'
  if [ -n "$suriq_key" ]; then
    printf 'GEMINI_API_KEY=%s\n' "$suriq_key" > .env
  fi
)
```

Load your local `.env` and start the processing service in Gemini mode. Restart the processing service after updating the key.

```bash
set -a
. ./.env
set +a
SURIQ_AI_PROVIDER=gemini .venv/bin/python -m uvicorn service.api:app --factory --host 127.0.0.1 --port 8765
```

Opt in for each non-confidential upload or retry that can use hosted assistance. Gemini receives selected text and PDF page images over HTTPS. Native extraction, original files, source validation, and verification stay local. Google may use free-tier submissions to improve its products. Use Ollama for confidential procurement documents. [Gemini terms](https://ai.google.dev/gemini-api/terms) · [Free-tier pricing](https://ai.google.dev/gemini-api/docs/pricing.md)

To check the live Gemini connection with a generated sample, load `.env` as above and run `SURIQ_AI_PROVIDER=gemini .venv/bin/python -m tests.live_model`.

## Local Ollama assistance

Install Ollama, start it in local-only mode, and download the initial model once. Its weights take approximately 3.3 GB of disk space. [Ollama local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features) · [Qwen3-VL 4B Instruct](https://ollama.com/library/qwen3-vl:4b-instruct)

```sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve
# In another terminal
ollama pull qwen3-vl:4b-instruct
```

Start the processing service with local AI enabled.

```sh
SURIQ_ENABLE_OLLAMA=1 .venv/bin/python -m uvicorn service.api:app --factory --host 127.0.0.1 --port 8765
```

SuriQ uses the installed model for scan-heavy, ambiguous, or unfamiliar sources. Model-assisted fields keep their parser-owned evidence and a review flag until confirmed against the source. Use **Suggest matches with local AI** in the quotation editor to request candidate BOM rows. Native parsing continues when the model is disabled or unavailable, and unresolved roles block verification. Start with a generous timeout on CPU-only hardware.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SURIQ_DB` | `.suriq/workspace.sqlite` | Local database |
| `SURIQ_API_PORT` | `8765` | Next.js proxy target. Match the Uvicorn port |
| `SURIQ_MAX_UPLOAD_MB` | `20` | Per-file size cap. Set the same value for both processes |
| `SURIQ_MAX_PAGES` | `50` | Maximum PDF pages |
| `SURIQ_MAX_ROWS` | `500` | Maximum populated workbook rows and row position |
| `SURIQ_TESSERACT` | `tesseract` | OCR executable |
| `SURIQ_OCR_LANGUAGE` | `eng` | Installed Tesseract language data |
| `SURIQ_OCR_CONFIDENCE` | `75` | OCR word score below which evidence needs review |
| `SURIQ_MONEY_DECIMALS` | `2` | Decimal half-up rounding precision |
| `SURIQ_SOFFICE` | `soffice` | LibreOffice executable for DOCX previews |
| `SURIQ_AI_PROVIDER` | `off` | Choose `gemini`, `ollama`, or `off`. If unset, `SURIQ_ENABLE_OLLAMA=1` selects Ollama |
| `GEMINI_API_KEY` | unset | Server-side Gemini API key, loaded from `.env` at launch. Also accepts the existing `.suriq/gemini-api-key` file |
| `SURIQ_GEMINI_TIMEOUT` | `30` | Timeout per Gemini attempt in seconds |
| `SURIQ_AI_MAX_CALLS` | `12` | Maximum interpretation windows per file |
| `SURIQ_ENABLE_OLLAMA` | unset | Legacy local AI switch |
| `SURIQ_OLLAMA_MODEL` | `qwen3-vl:4b-instruct` | Installed local vision-language model tag |
| `SURIQ_OLLAMA_TIMEOUT` | `180` | Timeout per model attempt in seconds |
| `SURIQ_OLLAMA_PORT` | `11434` | Loopback Ollama port |

See [architecture](ARCHITECTURE.md), [implementation status](IMPLEMENTATION_STATUS.md), and [test results](TEST_RESULTS.md).
