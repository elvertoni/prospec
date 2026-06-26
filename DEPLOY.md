# Deploy — App hospedado (Easypanel) + Agente local

Arquitetura dividida porque o Cloudflare Turnstile do TRF4 **barra scraping em
datacenter**: o navegador tem de ficar no PC do João (IP residencial + humano).

```
VPS / Easypanel                              PC do João
┌───────────────────────────────┐           ┌──────────────────────────┐
│ servidor FastAPI (Dockerfile)  │           │ agente/agente.py         │
│  • painel (basic auth)         │           │  • Chrome + CDP (Turnstile)│
│  • fila SQLite (volume)        │◀──poll────│  • puxa CNPJ da fila      │
│  • /api/ingest → Gemini → Sheets│◀──POST───│  • manda texto da sentença│
└───────────────────────────────┘           └──────────────────────────┘
```

## 1. Servidor no Easypanel

**Build:** este repo, `Dockerfile` na raiz (imagem slim, sem Playwright).

**Volume:** monte um volume em `/app/data` (guarda `fila.sqlite`).

**Credencial Sheets:** monte o `service_account.json` como arquivo (ex.
`/app/credentials/service_account.json`) ou cole via secret file.

**Variáveis de ambiente:**
```
AGENT_TOKEN=<token forte aleatório>      # compartilhado com o agente
PANEL_USER=<usuário do painel>
PANEL_PASS=<senha forte>
GEMINI_API_KEY=<chave>
GOOGLE_SHEETS_ID=<id da planilha>
GOOGLE_SA_JSON=/app/credentials/service_account.json
SHEETS_WORKSHEET=prospects
DB_PATH=/app/data/fila.sqlite
```

**Porta:** o container expõe `8000`. Aponte o domínio (Caddy do Easypanel cuida do HTTPS).

**Healthcheck:** `GET /health` → `{"ok": true}`.

## 2. Agente no PC do João

Precisa do repo + venv + Playwright (igual ao uso local). Configure
`agente/.env` (copie de `agente/.env.example`):
```
SERVER_URL=https://prospec.seudominio.com.br
AGENT_TOKEN=<mesmo token do servidor>
WORKER_NAME=joaopc
```

Rotina diária do João:
1. Abrir Chrome com debug e resolver o Turnstile uma vez:
   ```powershell
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" `
     --remote-debugging-port=9222 --user-data-dir="C:\PROJETOS\JOAO\data\chrome-debug"
   ```
2. Rodar o agente (processa a fila vinda do painel):
   ```powershell
   .venv\Scripts\python.exe -m agente.agente --loop
   ```

O João adiciona CNPJs pelo **painel hospedado** (de qualquer lugar); o agente no
PC dele consome a fila e devolve os prospects, que aparecem no painel.

## Próximo passo (escala)
- Trocar Sheets por Postgres (Easypanel provisiona) quando o volume crescer.
- Empacotar o agente como executável/tray para o João não usar terminal.
