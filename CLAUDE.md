# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local MVP that triages tax-law prospects for the Roveda e Marcelino law firm. You
type a CNPJ; it scrapes that party's lawsuits from **TRF4 (eProc / JFPR)**, opens
each sentence, reads the start of the report, classifies the **theme** via
**Gemini**, and appends a **3-column** row to a Google Sheet:
`NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO`. AI triages; the legal
decision stays human.

Deliberately small: one Streamlit screen, no server/queue/worker split. (Earlier
versions had a FastAPI server + SQLite queue + separate agent and a 16-column
sheet — that was removed as scope creep; see git history before `refac/mvp-3-colunas`
if you need it back.)

## Environment

- Windows + **PowerShell**. Python managed by **`uv`**, not global pip. Always call
  the venv interpreter explicitly: `.venv\Scripts\python.exe`.
- Setup:
  ```powershell
  uv venv .venv
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt
  .venv\Scripts\python.exe -m playwright install chromium
  ```
- Secrets in `.env` (copy `.env.example`): `GEMINI_API_KEY`, `GOOGLE_SHEETS_ID`,
  `GOOGLE_SA_JSON` (path to `credentials/service_account.json`), `SHEETS_WORKSHEET`.
  The Service Account email must be granted access to the Sheet.
- Never commit `.env` or `credentials/`.

## Commands

```powershell
# The whole app (also wrapped by iniciar.bat, which opens Chrome first)
.venv\Scripts\streamlit.exe run app.py

# Syntax / import smoke check after edits
.venv\Scripts\python.exe -c "from src import coletor_trf4, classificador, extrator, sheets, util"
```

No automated test suite. If adding tests, put them in `tests/` as `test_<module>.py`
and mock TRF4, Gemini, and Sheets — never hit live credentials or Cloudflare pages.

## The Cloudflare Turnstile constraint (why it must run locally)

`consulta.trf4.jus.br` sits behind Cloudflare Turnstile. **Pure automation is
flagged and fails even with a human solving the challenge**, and datacenter IPs are
blocked. The only thing that works: a **human solves the Turnstile once in a real
Chrome** opened with a debug port, then the collector **attaches to that live
session over CDP** (port 9222) and drives navigation. Sentences come back as
**HTML in an iframe**, not PDF.

So before any run, Chrome must be open with the debug port and the Turnstile solved
(`iniciar.bat` opens Chrome; the human solves Turnstile once):
```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 --user-data-dir="C:\PROJETOS\JOAO\data\chrome-debug"
```
This is why it can't run hosted in a datacenter — it needs a residential IP + a human.

## Architecture

Single process. `app.py` (Streamlit) is the only entry point and orchestrates the
`src/` pipeline directly:

```
app.py  →  coletor_trf4 (scrape)  →  extrator (trim)  →  classificador (Gemini)  →  sheets (append)
```

Flow: `Coletor.coletar_cnpjs` connects to the open Chrome via CDP, lists every
process for the CNPJ (JFPR, includes archived — **no pre-filter, varre todos**),
opens each, expands "próximos eventos", finds the Sentença row, reads the document
iframe text, and extracts the active-party name from the header
(`nome_parte_ativa`). `extrator.trecho_para_tema` isolates the start of the report.
`classificador.classificar_tema` returns just `{"tema_discussao": "..."}`.
`sheets.gravar(nome, numero, tema)` appends one row. **Idempotency** is by
`numero_processo` via `sheets.numeros_ja_gravados`, checked in `app.coletar`.

### Module map (`src/`)
- `coletor_trf4.py` — CDP scraper. `Coletor.coletar_cnpjs(cnpjs, limite=None)`
  yields `ProcessoColetado(cnpj, numero_processo, nome_parte, movimento, texto, erro)`.
  Skips processes with no merit sentence.
- `extrator.py` — `trecho_para_tema(texto)`: start of the report, capped ~1800 chars.
- `classificador.py` — single Gemini call. **`src/prompt.xml` is the System
  Instruction** (theme-only output). Returns the theme string.
- `sheets.py` — Google Sheets append + dedup. `CABECALHO` is the 3 columns.
- `util.py` — `so_digitos`, `CNJ` regex.

## Conventions

- Code and identifiers are **Portuguese** (`coletor`, `gravar`, `tema`). Match it.
- TRF4 DOM **selectors live in `config.yaml`**, not in code — the portal changes,
  so recalibrate there. Gemini model/params (`gemini:` block) also in `config.yaml`.
- Keep it lean: the value of this rewrite is 3 columns + one screen. Resist adding
  enrichment/ranking/extra columns unless the firm explicitly asks.
- Conventional Commit subjects (`feat: ...`), imperative, one change each.

## Caveats

- **LGPD / OAB ethics:** lawsuits are public, but prospecting must respect LGPD and
  the OAB code — validate with the firm.
- Respect TRF4 rate limits: human-paced requests, don't hammer the portal.
