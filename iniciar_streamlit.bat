@echo off
REM Painel Streamlit legado/local. O fluxo principal usa iniciar.bat.
cd /d "%~dp0"
".venv\Scripts\streamlit.exe" run dashboard.py
pause
