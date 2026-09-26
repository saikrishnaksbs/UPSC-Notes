# UPSC Notes Studio

Automatically turns newspapers, current-affairs magazines (Vision IAS, Vajirao & Reddy, Shankar IAS…), PIB releases, clippings, and your own notes into concise, exam-oriented Markdown notes — filed into the correct subject folders of this repository.

Everything runs **on your machine**. No paid APIs. No data leaves your laptop.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Running with Docker (Recommended)](#running-with-docker-recommended)
   - [macOS / Linux](#macos--linux)
   - [Windows](#windows)
   - [Environment Variable Overrides](#environment-variable-overrides)
4. [Running Locally Without Docker](#running-locally-without-docker)
5. [Web UI Walkthrough](#web-ui-walkthrough)
6. [CLI Commands (Batch Processing)](#cli-commands-batch-processing)
7. [Repository Structure](#repository-structure)
8. [Choosing a Model](#choosing-a-model)
9. [Troubleshooting](#troubleshooting)

---

## How It Works

```
PDF / image / .md / .txt   →   Extract text   →   Split into topics
→   Classify (subject, module)   →   Find existing notes
→   Write / update note   →   Review in Web UI   →   Apply to repo
```

Every generated note is traceable to its exact source text. Nothing is written to disk until you approve it.

---

## Prerequisites

You need **two** things installed on your machine regardless of whether you use Docker or not:

### 1. Docker Desktop
- Download and install from [https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)
- After install, open **Docker Desktop** and wait for it to be fully running (whale icon in menu bar turns solid)

### 2. Ollama (Local LLM)
- Download from [https://ollama.com](https://ollama.com) and install
- Pull the required models (only needed once):

```bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
```

> **Note**: `qwen2.5:7b` (~4.5 GB) is the default model for note generation. `nomic-embed-text` (~274 MB) is used for finding similar existing notes. Both are downloaded once and cached locally.

---

## Running with Docker (Recommended)

Docker handles all Python dependencies, Tesseract OCR, and system libraries automatically — no manual setup required.

### macOS / Linux

Open a terminal, navigate to this folder, then run:

```bash
./run_docker.sh
```

That's it. The script will:
1. Check Docker is running
2. Check Ollama is reachable on port 11434
3. Build the image (first run: ~5–10 min, subsequent runs: instant via cache)
4. Start the container and mount this repository into it

Then open **http://127.0.0.1:8765** in your browser.

---

### Windows

On Windows, use **PowerShell** or **Git Bash** and run:

```powershell
docker compose up --build
```

Then open **http://127.0.0.1:8765** in your browser.

> **Windows Ollama tip**: Ollama must be running before starting the container. If Docker can't reach Ollama, open Ollama settings and ensure it listens on `0.0.0.0` (not just `127.0.0.1`). Alternatively, pass `OLLAMA_BASE_URL=http://host.docker.internal:11434` (already the default in `docker-compose.yml`).

---

### Running with `docker run` Directly

If you prefer not to use Docker Compose:

```bash
# Build the image first (one time)
docker build -t upsc-notes-studio:latest -f notes_automation/Dockerfile notes_automation/

# Run the container
docker run -d \
  --name upsc-notes \
  -p 8765:8765 \
  -v "$(pwd)":/workspace \
  -v "$(pwd)/notes_automation/.data":/workspace/notes_automation/.data \
  --add-host=host.docker.internal:host-gateway \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  upsc-notes-studio:latest
```

Open **http://127.0.0.1:8765** in your browser.

To **stop** the container:
```bash
docker stop upsc-notes
```

To **restart** it (without rebuilding):
```bash
docker start upsc-notes
```

To **remove** the container:
```bash
docker rm -f upsc-notes
```

---

### Environment Variable Overrides

You can customise behaviour without editing any config file by passing environment variables:

| Variable | Default | Description |
|:---|:---|:---|
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | URL of Ollama — overrides LLM, embeddings & vision URLs |
| `UPSC_NOTES_MODEL` | `qwen2.5:7b` | LLM model name (e.g. `qwen2.5:14b`, `llama3.1:8b`) |
| `UPSC_NOTES_PORT` | `8765` | Port to expose the web UI on |
| `UPSC_NOTES_EXTRACTION_ENGINE` | `docling` | `docling` (best quality) or `pymupdf` (10× faster) |
| `UPSC_NOTES_OCR_ENGINE` | `tesseract` | `tesseract`, `ollama_vision`, or `none` |

Example — use a faster model and faster extraction:
```bash
UPSC_NOTES_MODEL=qwen2.5:14b UPSC_NOTES_EXTRACTION_ENGINE=pymupdf docker compose up
```

---

## Running Locally Without Docker

If you prefer to run without Docker (macOS only, requires `uv`, Ollama, and Tesseract):

```bash
# Install system dependencies
brew install ollama tesseract uv

# Pull LLM models
ollama pull qwen2.5:7b && ollama pull nomic-embed-text

# Install Python dependencies
cd notes_automation
uv sync

# Start the web server
uv run upsc-notes serve
```

Open **http://127.0.0.1:8765** in your browser.

---

## Web UI Walkthrough

Once the server is running at `http://127.0.0.1:8765`:

1. **Upload a file** — drag and drop a PDF, image, `.md`, or `.txt` file
2. **Add a tag** — e.g. `Vision IAS September 2026` (used in source traceability)
3. **Wait for processing** — topics are extracted and classified in real time
4. **Review proposals** — each topic shows: subject, module folder, confidence score, and a preview of the generated note
5. **Approve or skip** — approve topics you want to keep; edit titles/subjects inline if needed
6. **Apply approved** — click "Apply approved" to write notes to the repository

Approved notes are written to the correct subject subfolder (e.g. `Modern_history/freedom-struggle/`), with source traceability links added automatically.

---

## CLI Commands (Batch Processing)

Run commands via Docker Compose using `docker compose run`:

```bash
# List all processed jobs
docker compose run --rm upsc-notes jobs

# Ingest a file (batch mode)
docker compose run --rm upsc-notes ingest "/workspace/my-magazine.pdf" \
  --tag "Vision IAS September 2026" \
  --pages 1-60

# Show proposals for a job
docker compose run --rm upsc-notes show <job-id> --content

# Approve proposals above a confidence threshold
docker compose run --rm upsc-notes approve <job-id> --min-confidence 0.8

# Apply approved proposals (write to repo)
docker compose run --rm upsc-notes apply <job-id> --commit

# Unattended pipeline (ingest → auto-approve → apply) in one command
docker compose run --rm upsc-notes ingest "/workspace/my-magazine.pdf" \
  --tag "Vision IAS September 2026" \
  --auto-approve 0.85 --apply --commit
```

---

## Repository Structure

```
Upsc/
├── Ancient_history/               ← Subject folders
│   ├── MASTER-README.md
│   └── indus-valley-civilisation/ ← Module subfolders
│       ├── README.md
│       ├── 01-harappan-cities.md  ← Individual notes
│       └── ...
├── Art_and_culture/
├── Geography/
├── ... (15 more subjects)
├── _sources/                      ← Source traceability (auto-created)
│   ├── README.md
│   └── 2026-09-23-vision-ias/
│       └── 001-topic.md
├── notes_automation/              ← The automation pipeline
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── config.example.yaml        ← Copy to config.yaml to customise
│   └── src/upsc_notes/
├── docker-compose.yml             ← Root-level Compose (run from here)
└── run_docker.sh                  ← One-click launcher
```

---

## Choosing a Model

Copy `notes_automation/config.example.yaml` to `notes_automation/config.yaml` to set a default model. Or override per-session via the `UPSC_NOTES_MODEL` environment variable.

| Model | RAM needed | Speed | Notes |
|:---|:---|:---|:---|
| `qwen2.5:7b` (default) | ~8 GB | ~1 min/topic | Good balance |
| `qwen2.5:14b` | ~12 GB | ~2 min/topic | Better summaries |
| `qwen3:8b` | ~8 GB | ~1 min/topic | Set `think: false` in config |
| `llama3.1:8b` | ~8 GB | ~1 min/topic | Good alternative |

A 100-article magazine takes roughly **1.5–2 hours** with the 7B model.

---

## Troubleshooting

### ❌ "Cannot connect to Ollama"
- Make sure Ollama is running: open the Ollama app or run `ollama serve`
- On Linux, Ollama may only listen on `127.0.0.1`. Fix by setting: `OLLAMA_HOST=0.0.0.0 ollama serve`
- On Windows, check Ollama settings → listen on `0.0.0.0`

### ❌ Docker build fails / hangs
- Ensure Docker Desktop has at least **8 GB RAM** allocated (Settings → Resources → Memory)
- First build downloads PyTorch and Docling (~3 GB total) — this is a one-time download

### ❌ Port 8765 already in use
- Change the port: `UPSC_NOTES_PORT=9000 docker compose up`

### ❌ Notes not appearing / wrong folder
- Check that the subject folder exists in the repository (e.g. `Modern_history/`)
- The model only files notes into **existing** folders — it never creates new top-level subjects

### 🔎 View container logs
```bash
docker compose logs -f upsc-notes
```

### 🗑️ Full reset (delete jobs/cache, keep notes)
```bash
rm -rf notes_automation/.data
```
