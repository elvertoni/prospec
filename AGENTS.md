# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python MVP for tax-prospect triage. Core pipeline modules live in `src/`: collection (`coletor_trf4.py`), extraction (`extrator.py`), AI classification (`classificador.py`), ranking, Sheets integration, and orchestration (`pipeline.py`). The hosted FastAPI panel and queue are in `server/`, with HTML templates in `server/templates/`. The local polling worker for Joao's PC is in `agente/`. Runtime inputs and local artifacts belong under `data/`; credentials belong under `credentials/` and must not be committed. `dashboard.py` is the local Streamlit operational UI.

## Build, Test, and Development Commands

Create and populate the virtual environment with `uv`:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

Run the local pipeline from `data/cnpjs.txt`:

```powershell
.venv\Scripts\python.exe -m src.pipeline
```

Run one CNPJ with a process limit:

```powershell
.venv\Scripts\python.exe -m src.pipeline 81243735000148 --limite 5
```

Start the local dashboard with `.venv\Scripts\streamlit.exe run dashboard.py`. Start the hosted server locally with `.venv\Scripts\python.exe -m uvicorn server.app:app --reload`. Run the local worker with `.venv\Scripts\python.exe -m agente.agente --loop`.

## Coding Style & Naming Conventions

Use Python 3.12-compatible code, four-space indentation, and clear snake_case names for modules, functions, variables, and CLI flags. Keep modules focused around the existing pipeline boundaries. Prefer explicit configuration through `.env` and `config.yaml` instead of hard-coded paths, tokens, sheet IDs, or endpoints.

## Testing Guidelines

No formal test suite is currently present. When adding tests, place them under `tests/` and name files `test_<module>.py`. Mock external services such as TRF4, Gemini, Google Sheets, and the FastAPI queue; do not rely on live credentials or Cloudflare-protected pages in automated tests.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style subjects such as `feat: ...`. Keep subjects concise, imperative, and scoped to one change. Pull requests should describe the workflow affected, list manual verification commands, note required environment variables, and include screenshots for dashboard or hosted panel changes.

## Security & Configuration Tips

Never commit `.env`, `agente/.env`, `credentials/`, service account JSON files, or local SQLite data. Use `.env.example` and deployment docs to document required variables instead.
