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

:menu
cls
echo ================================================
echo  RMSA - Prospeccao Tributaria
echo ================================================
echo.
echo  Use sempre nesta ordem:
echo.
echo   1 - Preparar sistema
echo       Abre o painel e o Chrome correto do TRF4.
echo.
echo   Depois, no navegador:
echo       A) Adicione os CNPJs no painel.
echo       B) Resolva o Turnstile no Chrome do TRF4.
echo.
echo   2 - Processar fila
echo       Inicia o agente que consulta o TRF4.
echo.
echo -----------------------------------------------
echo  3 - Abrir painel
echo  4 - Voltar erros para pendente
echo  0 - Sair
echo -----------------------------------------------
echo.
set /p OPCAO="Escolha uma opcao: "

if "%OPCAO%"=="1" goto preparar
if "%OPCAO%"=="2" goto agente
if "%OPCAO%"=="3" goto painel
if "%OPCAO%"=="4" goto reset_erros
if "%OPCAO%"=="0" exit /b 0
goto menu

:validar_venv
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo ERRO: ambiente .venv nao encontrado.
  echo Rode o setup do README antes de usar o sistema.
  pause
  goto menu
)
exit /b 0

:garantir_env_agente
if not exist "agente\.env" (
  echo Criando agente\.env local...
  > "agente\.env" echo # Configuracao local do agente. Nao commitar.
  >> "agente\.env" echo SERVER_URL=%PAINEL_URL%
  >> "agente\.env" echo AGENT_TOKEN=%AGENT_TOKEN%
  >> "agente\.env" echo WORKER_NAME=joaopc
)
exit /b 0

:garantir_servidor
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest '%PAINEL_URL%/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo Iniciando servidor local do painel...
  start "RMSA - servidor local" cmd /k "cd /d ""%~dp0"" && set PANEL_USER=%PANEL_USER%&& set PANEL_PASS=%PANEL_PASS%&& set AGENT_TOKEN=%AGENT_TOKEN%&& set DB_PATH=%DB_PATH%&& .venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000"
  timeout /t 3 /nobreak >nul
) else (
  echo Servidor local ja esta ativo.
)
exit /b 0

:preparar
call :validar_venv
call :garantir_env_agente
call :garantir_servidor
echo.
echo Abrindo painel...
start "" "%PAINEL_URL%"

if exist "%CHROME_EXE%" (
  echo Abrindo Chrome correto do TRF4...
  start "RMSA - Chrome TRF4" "%CHROME_EXE%" --remote-debugging-port=9222 --user-data-dir="%CD%\data\chrome-debug" "%TRF4_URL%"
) else (
  echo.
  echo AVISO: Chrome nao encontrado.
  echo Abra manualmente o Google Chrome com --remote-debugging-port=9222.
)

echo.
echo AGORA FACA SO ISTO:
echo.
echo  1. No painel, adicione os CNPJs.
echo  2. No Chrome do TRF4, resolva o Turnstile.
echo  3. Volte aqui e escolha a opcao 2 - Processar fila.
echo.
pause
goto menu

:painel
call :validar_venv
call :garantir_servidor
start "" "%PAINEL_URL%"
goto menu

:agente
call :validar_venv
call :garantir_env_agente
call :garantir_servidor
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest 'http://127.0.0.1:9222/json/version' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo.
  echo ERRO: o Chrome correto do TRF4 ainda nao esta pronto.
  echo.
  echo Escolha primeiro a opcao 1 - Preparar sistema.
  echo Depois resolva o Turnstile no Chrome do TRF4.
  echo So entao volte aqui e escolha a opcao 2.
  echo.
  pause
  goto menu
)

echo.
echo Agente iniciado.
echo Deixe esta janela aberta enquanto ele processa.
echo Para parar, pressione Ctrl+C.
echo.
".venv\Scripts\python.exe" -m agente.agente --loop
pause
goto menu

:reset_erros
call :validar_venv
".venv\Scripts\python.exe" -c "import sqlite3,time; c=sqlite3.connect('data/fila.sqlite'); cur=c.execute(\"UPDATE jobs SET status='pendente', worker=NULL, erro=NULL, atualizado_em=? WHERE status='erro'\", (time.time(),)); c.commit(); print('Itens voltados para pendente:', cur.rowcount); c.close()"
pause
goto menu
