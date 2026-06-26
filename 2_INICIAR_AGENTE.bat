@echo off
setlocal
cd /d "%~dp0"

set "PAINEL_URL=http://127.0.0.1:8000"

if not exist ".venv\Scripts\python.exe" (
  echo ERRO: ambiente .venv nao encontrado.
  pause
  exit /b 1
)

if not exist "agente\.env" (
  echo ERRO: agente\.env nao existe.
  echo Execute 1_ABRIR_PAINEL_E_CHROME.bat uma vez ou crie agente\.env.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest '%PAINEL_URL%/health' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo ERRO: painel local nao esta respondendo em %PAINEL_URL%.
  echo Execute primeiro: 1_ABRIR_PAINEL_E_CHROME.bat
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest 'http://127.0.0.1:9222/json/version' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo ERRO: Chrome do TRF4 nao esta aberto na porta 9222.
  echo Execute primeiro: 1_ABRIR_PAINEL_E_CHROME.bat
  echo Depois resolva o Turnstile no Chrome que abriu.
  pause
  exit /b 1
)

echo.
echo Agente iniciado. Deixe esta janela aberta.
echo Para parar, pressione Ctrl+C.
echo.
".venv\Scripts\python.exe" -m agente.agente --loop
pause
