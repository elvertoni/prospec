# JOAO — Prospecção de Teses Tributárias (Roveda e Marcelino Sociedade de Advogados)

MVP que automatiza a triagem de prospects: busca processos tributários no
**TRF4 (eProc / JFPR)** por CNPJ, lê a sentença, classifica a tese de direito
tributário via IA (Gemini) e grava numa planilha Google para os advogados
validarem. Dashboard em Streamlit para priorizar oportunidades.

> Triagem assistida por IA para **validação humana** — não é parecer jurídico.

## Pipeline

```
data/cnpjs.txt
   → coletor_trf4.py   (Playwright: busca CNPJ, baixa PDF da sentença)
   → extrator.py       (PDF → relatório + dispositivo em texto)
   → classificador.py  (Gemini + src/prompt.xml → JSON da triagem)
   → sheets.py         (append na Google Sheet)
   → dashboard.py      (Streamlit lê a planilha)
```

## Setup

```powershell
# 1. dependências (venv via uv — Python global é gerido por uv, não use pip global)
uv venv .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium

# 2. credenciais
copy .env.example .env   # preencher GEMINI_API_KEY, GOOGLE_SHEETS_ID, GOOGLE_SA_JSON
# Google Sheets: criar Service Account no Google Cloud, baixar JSON para
# credentials/service_account.json e COMPARTILHAR a planilha com o e-mail da SA.
```

## Uso

```powershell
# (todos os comandos usam o python do venv)

# lote a partir de data/cnpjs.txt
.venv\Scripts\python.exe -m src.pipeline

# CNPJ(s) direto
.venv\Scripts\python.exe -m src.pipeline 11222333000181

# modo semi-manual (se o TRF4 travar em CAPTCHA: João baixa o PDF, IA processa)
.venv\Scripts\python.exe -m src.pipeline --pdf data\pdfs\exemplo.pdf --numero 5030399-41.2011.4.04.7000 --nome "Empresa X"

# dashboard
.venv\Scripts\streamlit.exe run dashboard.py
```

## TRF4: coleta via CDP (Cloudflare Turnstile)

O `consulta.trf4.jus.br` está atrás de **Cloudflare Turnstile** (anti-bot).
Automação pura (Playwright lançando o browser) é flagada e o Turnstile **falha
mesmo com humano resolvendo** — confirmado 2026-06. Solução que funciona:

**Um humano resolve o Turnstile uma vez no Chrome normal; o coletor anexa nessa
sessão via CDP e dirige a navegação.** A sentença vem como **HTML** (sem PDF).

### Passo a passo

1. **Feche todo Chrome** e abra um com porta de debug + perfil separado:
   ```powershell
   Get-Process chrome -ErrorAction SilentlyContinue | Stop-Process -Force
   & "C:\Program Files\Google\Chrome\Application\chrome.exe" `
     --remote-debugging-port=9222 `
     --user-data-dir="C:\PROJETOS\JOAO\data\chrome-debug"
   ```
2. Na janela que abrir, vá na consulta do CNPJ e **resolva o Turnstile** (deixe
   a lista de processos carregar). URL de exemplo:
   `https://consulta.trf4.jus.br/trf4/controlador.php?acao=consulta_processual_valida_pesquisa&selForma=CP&txtValor=<CNPJ>&selOrigem=PR&chkMostrarBaixados=S`
3. Rode o pipeline — ele conecta no Chrome aberto (porta 9222):
   ```powershell
   .venv\Scripts\python.exe -m src.pipeline 81243735000148 --limite 5
   ```

### Como o coletor opera (`src/coletor_trf4.py`)

- Lista os processos do CNPJ (JFPR, inclui baixados).
- Em cada processo: clica **"mostrar os próximos eventos"**, acha a linha de
  **Sentença** (`table.tabela`), abre o documento (`acessar_documento_publico`)
  e lê o **texto do iframe**.
- Extrai o **nome do polo ativo** do cabeçalho (`AUTOR/IMPETRANTE/...`).
- Processos sem sentença de mérito são pulados.

`--limite N` limita processos por CNPJ (teste/throttle).

**Fallback:** modo **semi-manual** `--pdf` (classifica um PDF de sentença baixado
à mão) continua disponível para casos avulsos.

## Migração para a stack padrão (Django)

Os módulos `src/` são desacoplados (coletor, extrator, classificador, sheets),
então a migração troca só as bordas:
- `sheets.py` → models + Postgres
- `dashboard.py` → views Django + HTMX
- `pipeline.py` → task Celery/management command
- `coletor_trf4.py` e `classificador.py` reaproveitados como serviços.

## Avisos

- **LGPD / uso de dados:** processos consultados são públicos, mas o uso para
  prospecção deve respeitar a LGPD e o Código de Ética da OAB. Validar com o
  escritório antes de operacionalizar.
- **Termos do TRF4:** respeitar robots/limites de requisição do portal; usar
  ritmo humano e não sobrecarregar o serviço.
- Nunca comitar `.env` nem `credentials/`.
