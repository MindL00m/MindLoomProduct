# Main Loom backend: API, ingestion, retrieval, integrations, and captures.
# Pinned to the explicit -slim-bookworm tag: on some Docker Desktop image
# stores the floating `python:3.11-slim` manifest resolves to a mismatched
# layer that fails with "exec format error" on arm64.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Pre-baked tiktoken cache so the cl100k_base encoding is not downloaded
    # on first request at runtime.
    TIKTOKEN_CACHE_DIR=/app/.tiktoken_cache

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Warm the tiktoken encoding cache into the image.
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" \
    && python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
