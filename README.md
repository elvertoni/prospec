# JOAO — Prospecção de Teses Tributárias (Roveda e Marcelino Sociedade de Advogados)

MVP local: digita CNPJ → o sistema entra no **TRF4 (eProc / JFPR)**, varre os
processos da parte, abre cada sentença, lê o começo do relatório, classifica o
**tema** via IA (Gemini) e preenche uma planilha Google de **3 colunas**:

| NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO |
|---|---|---|
| Grupo O Boticário | 5030399-41.2011.4.04.7000 | PIS/COFINS |

> Triagem assistida por IA para **validação humana** — não é parecer jurídico.

## Pipeline

```
app.py (Streamlit: campo CNPJ + botão)
   → src/coletor_trf4.py   (CDP no Chrome real: lista, abre, acha sentença, lê texto)
   → src/extrator.py       (isola o trecho do relatório que revela o tema)
   → src/classificador.py  (Gemini → tema em rótulo curto)
   → src/sheets.py         (append [nome, numero, tema])
```

## Por que precisa do Chrome local (Cloudflare Turnstile)

`consulta.trf4.jus.br` está atrás de **Cloudflare Turnstile**. Automação pura é
flagada e o Turnstile **falha mesmo com humano resolvendo**; IP de datacenter é
bloqueado. Solução que funciona (confirmada 2026-06): um humano resolve o
Turnstile **uma vez** num Chrome normal aberto com porta de debug; o coletor
**anexa nessa sessão via CDP** e dirige a navegação. A sentença vem como **HTML**.

## Setup

```powershell
# dependências (venv via uv — não use pip global)
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

# credenciais
copy .env.example .env   # GEMINI_API_KEY, GOOGLE_SHEETS_ID, GOOGLE_SA_JSON, SHEETS_WORKSHEET
# Google Sheets: criar Service Account, baixar JSON p/ credentials/service_account.json
# e COMPARTILHAR a planilha com o e-mail da SA.
```

## Uso

**Duplo clique em `iniciar.bat`** — ele fecha o Chrome, reabre com a porta de
debug, e sobe o painel. No painel:

1. No Chrome que abriu, faça a consulta de um CNPJ e **resolva o Turnstile** uma vez.
2. No painel, cole os CNPJs (um por linha) e clique **▶ Coletar**.
3. A planilha enche; a tela mostra as linhas novas. `Limite por CNPJ` corta o nº
   de processos (teste/throttle); `0` = todos.

Manual: `.venv\Scripts\streamlit.exe run app.py` (com o Chrome já aberto na 9222).

## Avisos

- **LGPD / OAB:** processos são públicos, mas o uso para prospecção deve respeitar
  a LGPD e o Código de Ética da OAB. Validar com o escritório.
- **TRF4:** respeitar limites do portal; ritmo humano, não sobrecarregar.
- Nunca comitar `.env` nem `credentials/`.
