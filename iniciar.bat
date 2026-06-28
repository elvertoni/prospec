@echo off
chcp 65001 >nul
title Prospeccao Tributaria TRF4
cd /d "%~dp0"

echo ============================================================
echo   PROSPECCAO TRIBUTARIA TRF4
echo ============================================================
echo.
echo  1) Fechando Chrome e abrindo com a porta de depuracao...
taskkill /IM chrome.exe /F >nul 2>&1
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%~dp0data\chrome-debug"

echo  2) No Chrome que abriu: faca a consulta de um CNPJ e resolva
echo     o Turnstile (Cloudflare) UMA vez. Deixe a janela aberta.
echo.
echo  3) Abrindo o painel no navegador...
echo.
".venv\Scripts\streamlit.exe" run app.py

pause
