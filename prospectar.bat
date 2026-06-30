@echo off
chcp 65001 >nul
title Prospeccao TRF4 — Automatizada
cd /d "%~dp0"

echo ============================================================
echo   PROSPECCAO AUTOMATICA — CNPJ: %1
echo ============================================================
echo.

if "%~1"=="" (
    echo ❌ Informe o CNPJ como parametro.
    echo    Exemplo: prospectar.bat 79430682000122
    pause
    exit /b 1
)

set CNPJ=%~1

echo [1/4] Abrindo Chrome com porta de depuracao...
taskkill /IM chrome.exe /F >nul 2>&1
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0data\chrome-debug" "https://consulta.trf4.jus.br/trf4/controlador.php?acao=consulta_processual_pesquisa"

echo.
echo [2/4] ⚠️  RESOLVA O TURNSTILE (Cloudflare) NO CHROME QUE ABRIU.
echo      Depois pressione qualquer tecla para continuar...
pause >nul

echo.
echo [3/4] Enfileirando CNPJ %CNPJ% e iniciando coleta...
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'.'); from src.fila import criar_lote; from src.util import so_digitos; cnpj = so_digitos('%CNPJ%'); lote_id = criar_lote([cnpj]); print(f'Lote #{lote_id} criado com CNPJ {cnpj}')"

echo.
echo [4/4] Iniciando worker (processamento automatico)...
echo      O worker vai listar, abrir processos, classificar e gravar na Sheets.
echo      ⚠️  Se o Turnstile reaparecer, resolva no Chrome.
echo.
.venv\Scripts\python.exe worker.py

echo.
echo ============================================================
echo   ✅ CONCLUIDO! Verifique a planilha:
echo   https://docs.google.com/spreadsheets/d/1TPcwqyoogdKYe3hYcl8t-WCaJ84XPVubvHz7nJ9C4U0
echo ============================================================
pause
