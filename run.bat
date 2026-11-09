@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv (
  echo Первый запуск: создаю окружение...
  python -m venv .venv || (echo Нужен Python 3.9+ с python.org & pause & exit /b)
)
.venv\Scripts\pip install -q -r requirements.txt
.venv\Scripts\python app.py
pause
