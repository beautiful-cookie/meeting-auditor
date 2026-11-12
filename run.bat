@echo off
rem AUDITOR_TEST=1 — для автотеста: не открывать Блокнот и браузер, не ждать нажатия клавиши
chcp 65001 >nul
cd /d "%~dp0"
title Аудитор встреч

rem ---- 1. Python ----
set "PY=python"
%PY% --version >nul 2>nul || set "PY=py"
%PY% --version >nul 2>nul || goto :nopython
%PY% -c "import sys; sys.exit(sys.version_info < (3, 10))" || goto :oldpython

rem ---- 2. Компоненты (только при первом запуске) ----
if exist .venv\Scripts\python.exe goto :settings
echo Первый запуск: устанавливаю нужные компоненты, это займёт 1-2 минуты...
%PY% -m venv .venv || goto :fail
.venv\Scripts\python -m pip install --disable-pip-version-check -q -r requirements.txt || goto :fail

rem ---- 3. Файл настроек с ключами ----
:settings
if exist .env goto :run
copy .env.example .env >nul
echo.
echo Создан файл настроек .env, сейчас он откроется в Блокноте.
echo Впишите ключи после знака = , сохраните файл (Ctrl+S) и запустите run.bat ещё раз.
if not defined AUDITOR_TEST start "" notepad .env
if not defined AUDITOR_TEST pause
exit /b 0

rem ---- 4. Запуск ----
:run
echo.
echo Аудитор встреч запущен: http://localhost:8000
echo Страница откроется в браузере сама. Чтобы остановить приложение, закройте это окно.
echo.
if not defined AUDITOR_TEST start "" /min cmd /c "timeout /t 3 >nul & start http://localhost:8000"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if not defined AUDITOR_TEST pause
exit /b 0

:nopython
echo Не найден Python.
echo Установите его с https://www.python.org/downloads/ и при установке отметьте галочку
echo "Add python.exe to PATH", затем запустите run.bat ещё раз.
if not defined AUDITOR_TEST pause
exit /b 1

:oldpython
echo Нужен Python версии 3.10 или новее. Установите свежую версию с https://www.python.org/downloads/
if not defined AUDITOR_TEST pause
exit /b 1

:fail
echo Не удалось установить компоненты. Проверьте подключение к интернету и запустите run.bat ещё раз.
if exist .venv rmdir /s /q .venv
if not defined AUDITOR_TEST pause
exit /b 1
