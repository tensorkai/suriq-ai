# SuriQ AI

SuriQ AI is a local procurement verification workspace for Philippine construction teams. A batch links one bill of materials to up to five supplier quotations. It shows discrepancies as issue tabs with the original PDF pages or spreadsheet cells highlighted.

## Install and run

Use a Bash-compatible shell on Linux or Windows with WSL2. Install Node.js 20.9+ and Python 3.12+. The implementation was verified with Node.js 24 and Python 3.14.

Install Poppler and Tesseract with English language data. On Ubuntu or Debian:

```sh
sudo apt install poppler-utils tesseract-ocr tesseract-ocr-eng python3-venv
```

On Fedora:

```sh
sudo dnf install poppler-utils tesseract tesseract-langpack-eng
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
2. Upload the BOM and assign a supplier and coverage status to each quotation. Accepted files are PDF and XLSX.
3. Inspect the **Material lines**, **Document terms**, and **Original source** views. For a PDF correction, draw a box and transcribe its source text. For XLSX, select the original cell. Enter a note when supplying a packaging conversion.
4. Use **Set scope** on the BOM to select all or specific rows. Coverage defaults to unknown for supplier offers.
5. Run verification. Review the grouped source cards in **Findings** and save a decision. Use **Focus highlights** to center the relevant regions across the linked documents.
6. Review the supplier matrix, including reasons for incomparable totals, then download PDF or CSV from **Export**.

If extraction fails or the service restarts during processing, use **Retry extraction** or replace the affected file. Check the original when extraction returns few rows or flags OCR. The SQLite database is created at `.suriq/workspace.sqlite` and contains the originals, results, and audit history. Stop the service before copying the database as a backup. Treat backups and downloaded reports as procurement records.

## Optional local matching assistance

Install Ollama in the same environment as the Python service. Set `OLLAMA_NO_CLOUD=1` for the Ollama server, restart it, and pull the local model during setup. [Ollama local-only mode](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features) · [Qwen3 4B](https://ollama.com/library/qwen3:4b)

```sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve
# In another terminal
ollama pull qwen3:4b
```

Set `SURIQ_ENABLE_OLLAMA=1` when starting the Python service. In a quotation's material editor, select **Suggest matches with local AI**. The model returns candidate BOM row IDs for manual confirmation. Verification, commercial calculations, and exports remain available with assistance disabled or unavailable.

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
| `SURIQ_ENABLE_OLLAMA` | unset | Set to `1` to enable on-demand suggestions |
| `SURIQ_OLLAMA_MODEL` | `qwen3:4b` | Local model tag |
| `SURIQ_OLLAMA_PORT` | `11434` | Loopback Ollama port |

See [architecture](ARCHITECTURE.md), [implementation status](IMPLEMENTATION_STATUS.md), and [test results](TEST_RESULTS.md).
