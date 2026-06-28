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

echo  2) Abrindo o painel no navegador (aguarde alguns segundos)...
start "" /b cmd /c "timeout /t 6 >nul & start "" http://localhost:8501"

echo.
echo  Quando o painel abrir: cole o CNPJ e clique em Iniciar Coleta.
echo  Se o Turnstile (Cloudflare) aparecer no Chrome, resolva na janela.
echo.
".venv\Scripts\streamlit.exe" run app.py --server.headless=true --server.port=8501

pause
