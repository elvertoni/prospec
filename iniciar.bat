@echo off
REM Duplo clique para abrir o painel de prospeccao no navegador.
cd /d "%~dp0"
".venv\Scripts\streamlit.exe" run dashboard.py
pause
