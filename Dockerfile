FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl findutils zstd \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

COPY app ./app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ARG ADMIN_PASSWORD=admin
ENV ADMIN_PASSWORD=${ADMIN_PASSWORD} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_MODELS=/opt/ollama/models \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    HF_HOME=/opt/hf \
    RAG_CHAT_MODEL=qwen2.5:1.5b \
    PORT=8000

RUN set -eu; \
    python -m app.seed; \
    ollama serve & \
    server=$!; \
    i=0; \
    until curl -sf http://127.0.0.1:11434/api/tags >/dev/null; do \
      i=$((i + 1)); \
      if [ "$i" -gt 60 ]; then echo "Ollama did not start during build" >&2; exit 1; fi; \
      sleep 1; \
    done; \
    ollama pull "$RAG_CHAT_MODEL"; \
    kill "$server"; \
    wait "$server" || true

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
