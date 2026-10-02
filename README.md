# Clinic chart

A local question-answering app over synthetic clinic records: 100 clients, 400 appointments, and 500 bloodwork rows. Staff can read every chart and add rows. A patient can read and ask about only their own chart.

The chat model is `qwen2.5:1.5b` through [Ollama](https://ollama.com). Embeddings are `all-MiniLM-L6-v2`. Both stay on the machine running the app.

## Sign in

Open http://127.0.0.1:8000

- Staff password: `admin`
- Patient: MRN `MRN-10001`, password `demo` (Avery Chen). Every seeded patient uses `demo`.

A short answer takes a few seconds, and longer when the computer is low on memory.

## Run with Docker

Install Docker, then from this folder:

```bash
docker build -t clinic-chart .
docker run --rm -p 8000:8000 clinic-chart
```

The image listens on port 8000. The build installs Python, Ollama, the chat model, the embedding model, and the seeded database, so the first build takes a while and the image is large.

To set the staff password before the database is created:

```bash
docker build --build-arg ADMIN_PASSWORD=your-password -t clinic-chart .
```

The password is stored when the database is seeded. A later `docker run -e ADMIN_PASSWORD=...` does not change a database that already exists.

To keep added rows across container recreations, mount a data volume. An empty volume is seeded on first start:

```bash
docker run --rm -p 8000:8000 -v clinic-data:/app/data clinic-chart
```

## Run without Docker

Use this when you want the app directly on the machine. It is two processes: Ollama, and the Python server.

### Python 3.12

PyTorch on this project needs Python 3.12. The `python3` on the Intel Mac used to build this repo is 3.14, and PyTorch has no wheel for that version.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

If `python3.12` is not installed, install it from [python.org](https://www.python.org/downloads/) or with [uv](https://docs.astral.sh/uv/):

```bash
uv python install 3.12
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### Ollama

Install Ollama from https://ollama.com/download so the `ollama` command is on your PATH. Then, in one terminal:

```bash
ollama serve
```

In a second terminal:

```bash
ollama pull qwen2.5:1.5b
```

If the Ollama app is already running, skip `ollama serve`. On the machine where this repo was first set up, a binary is at `.tools/ollama/ollama` and the model may already be downloaded.

### Database and server

From the project folder, with the virtualenv active:

```bash
python -m app.seed
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`python -m app.seed` loads the synthetic charts. Running it again leaves existing rows in place. Replace them with `python -m app.seed --reset`.

Leave `ollama serve` running in the other terminal. Open http://127.0.0.1:8000.
