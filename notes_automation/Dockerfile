# syntax=docker/dockerfile:1
# UPSC Notes Studio: the web app, Docling (with its AI models baked in) and Tesseract OCR.
# The LLM runs in the separate "ollama" service (see docker-compose.yml).
FROM python:3.12-slim-bookworm

# tesseract: OCR for images/scans (Apple Vision OCR is macOS-only), git: optional commits,
# curl: healthcheck, libgl1/libglib: needed by Docling's image stack.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-eng git curl libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_CACHE=1 \
    HF_HOME=/opt/models/hf \
    HOME=/tmp

WORKDIR /app

# CPU-only PyTorch first: the default wheel pulls ~2 GB of CUDA libraries that Docling doesn't need here.
RUN uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md config.example.yaml ./
COPY src/ ./src/
RUN uv pip install .

# Download Docling's layout + table models now, so the container works offline.
RUN python - <<'EOF'
import tempfile, pathlib, fitz
from upsc_notes.ingest.docling_engine import docling_blocks
pdf = pathlib.Path(tempfile.mkdtemp()) / "warmup.pdf"
doc = fitz.open(); page = doc.new_page()
page.insert_text((72, 72), "Warm-up heading", fontsize=18)
page.insert_text((72, 110), "Some body text to run the layout and table models once.")
doc.save(pdf)
print(len(docling_blocks(pdf, [1], ocr=False)), "blocks: Docling models cached")
EOF
RUN chmod -R a+rwX /opt/models

# Settings: the notes repo is mounted at /workspace; its notes_automation/config.yaml is used if present.
ENV HF_HUB_OFFLINE=1 \
    UPSC_NOTES_REPO=/workspace \
    UPSC_NOTES_CONFIG=/workspace/notes_automation/config.yaml \
    UPSC_NOTES_HOST=0.0.0.0 \
    UPSC_NOTES_PORT=8765 \
    OLLAMA_BASE_URL=http://ollama:11434

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fs http://localhost:8765/api/status || exit 1

ENTRYPOINT ["upsc-notes"]
CMD ["serve", "--no-browser"]
