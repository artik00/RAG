#!/bin/sh
set -eu

echo "Starting Ollama"
ollama serve &
ollama_pid=$!

cleanup() {
  kill "$ollama_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Waiting for Ollama"
i=0
until curl -sf http://127.0.0.1:11434/api/tags >/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "Ollama did not start" >&2
    exit 1
  fi
  sleep 1
done

model="${RAG_CHAT_MODEL:-qwen2.5:1.5b}"
echo "Ensuring chat model ${model}"
ollama pull "$model"

if [ ! -s /app/data/rag.db ]; then
  echo "Seeding the clinic database"
  python -m app.seed
fi

port="${PORT:-8000}"
echo "Chart is listening on port ${port}"
python -m uvicorn app.main:app --host 0.0.0.0 --port "$port"
