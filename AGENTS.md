# Repository Guidelines

## Project Structure & Module Organization

This repository is a Python MVP for tax-prospect triage. It is a single local Streamlit app: `app.py` orchestrates the `src/` pipeline — collection (`coletor_trf4.py`), extraction (`extrator.py`), AI theme classification (`classificador.py`), and Sheets integration (`sheets.py`). Output is a 3-column Google Sheet (`NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO`). Runtime inputs and local artifacts belong under `data/`; credentials belong under `credentials/` and must not be committed. There is no server/queue/worker — scraping runs locally because the TRF4 Cloudflare Turnstile blocks datacenters.

## Build, Test, and Development Commands

Create and populate the virtual environment with `uv`:

```powershell
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

Run the app (Chrome with `--remote-debugging-port=9222` must be open with the Turnstile solved):

```powershell
.venv\Scripts\streamlit.exe run app.py
```

`iniciar.bat` wraps this: it opens the debug Chrome and starts Streamlit. After editing, smoke-check imports:

```powershell
.venv\Scripts\python.exe -c "from src import coletor_trf4, classificador, extrator, sheets, util"
```

## Coding Style & Naming Conventions

Use Python 3.12-compatible code, four-space indentation, and clear snake_case names for modules, functions, variables, and CLI flags. Keep modules focused around the existing pipeline boundaries. Prefer explicit configuration through `.env` and `config.yaml` instead of hard-coded paths, tokens, sheet IDs, or endpoints.

## Testing Guidelines

No formal test suite is currently present. When adding tests, place them under `tests/` and name files `test_<module>.py`. Mock external services such as TRF4, Gemini, and Google Sheets; do not rely on live credentials or Cloudflare-protected pages in automated tests.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style subjects such as `feat: ...`. Keep subjects concise, imperative, and scoped to one change. Pull requests should describe the workflow affected, list manual verification commands, note required environment variables, and include screenshots for dashboard or hosted panel changes.

## Security & Configuration Tips

Never commit `.env`, `agente/.env`, `credentials/`, service account JSON files, or local SQLite data. Use `.env.example` and deployment docs to document required variables instead.
