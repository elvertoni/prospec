@echo off
setlocal
cd /d "%~dp0"

set "PANEL_USER=admin"
set "PANEL_PASS=trocar"
set "AGENT_TOKEN=local-dev-token"
set "DB_PATH=data\fila.sqlite"
set "PAINEL_URL=http://127.0.0.1:8000"
set "TRF4_URL=https://consulta.trf4.jus.br/trf4/controlador.php?acao=principal"
set "CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if not exist ".venv\Scripts\python.exe" (
  echo ERRO: ambiente .venv nao encontrado.
  echo Rode o setup do README antes de usar este atalho.
  pause
  exit /b 1
)

if not exist "agente\.env" (
  echo Criando agente\.env local...
  > "agente\.env" echo # Configuracao local do agente. Nao commitar.
  >> "agente\.env" echo SERVER_URL=%PAINEL_URL%
  >> "agente\.env" echo AGENT_TOKEN=%AGENT_TOKEN%
  >> "agente\.env" echo WORKER_NAME=joaopc
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest '%PAINEL_URL%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo Iniciando servidor local do painel...
  start "RMSA - servidor local" cmd /k "cd /d ""%~dp0"" && set PANEL_USER=%PANEL_USER%&& set PANEL_PASS=%PANEL_PASS%&& set AGENT_TOKEN=%AGENT_TOKEN%&& set DB_PATH=%DB_PATH%&& .venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000"
  timeout /t 3 /nobreak >nul
) else (
  echo Servidor local ja esta ativo.
)

echo Abrindo painel...
start "" "%PAINEL_URL%"

if exist "%CHROME_EXE%" (
  echo Abrindo Chrome do TRF4 com porta 9222...
  start "RMSA - Chrome TRF4" "%CHROME_EXE%" --remote-debugging-port=9222 --user-data-dir="%CD%\data\chrome-debug" "%TRF4_URL%"
) else (
  echo AVISO: Chrome nao encontrado nos caminhos padrao.
  echo Abra manualmente com --remote-debugging-port=9222.
)

echo.
echo Proximo passo:
echo 1. No painel, adicione os CNPJs.
echo 2. No Chrome do TRF4, resolva o Turnstile.
echo 3. Depois execute: 2_INICIAR_AGENTE.bat
echo.
pause
