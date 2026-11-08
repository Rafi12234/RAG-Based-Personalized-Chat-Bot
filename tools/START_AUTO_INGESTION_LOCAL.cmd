@echo off
setlocal EnableExtensions
rem Local development only. Run AFTER Docker Desktop / docker compose services start.
rem Launches the existing verified live-pilot Celery worker and guarded scheduler.
rem NEVER starts processing in production and NEVER targets protected QA fixtures.
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "WORKER=%ROOT%\python-worker"
if not exist "%WORKER%\.venv\Scripts\python.exe" (
    echo ERROR: Missing Python virtual environment: "%WORKER%\.venv"
    echo Complete the existing Python worker setup first.
    exit /b 1
)
if /I "%NODE_ENV%"=="production" (
    echo ERROR: Local automatic ingestion cannot run in production mode.
    exit /b 1
)
echo Starting local automatic ingestion for NEW uploads in all eligible knowledge bases.
echo The Phase 2 and Phase 3 protected QA fixtures remain excluded.
echo Keep both windows running. Stop any OLD live-pilot worker and scoped watcher
echo before executing this launcher to avoid competing processes.
start "DocMind Local Ingestion Worker" /D "%WORKER%" cmd /k "set DOCMIND_ENABLE_PILOT_INGESTION=YES_FOR_TESTING&& .venv\Scripts\python.exe -m celery -A worker.celery_app:celery_app worker --pool=solo --concurrency=1 --loglevel=info --include=worker.live_ingestion_task --queues=docmind_phase2_live"
if errorlevel 1 (
    echo ERROR: Could not start ingestion worker.
    exit /b 1
)
start "DocMind Local Auto Dispatcher" /D "%WORKER%" cmd /k "set DOCMIND_ENABLE_MULTI_KB_INGESTION=YES_FOR_LOCAL_TESTING&& set DOCMIND_ENABLE_AUTODISPATCH=YES_FOR_TESTING&& .venv\Scripts\python.exe -m worker.auto_ingestion_service --watch --interval-seconds 15"
if errorlevel 1 (
    echo ERROR: Could not start ingestion dispatcher.
    exit /b 1
)
echo Opened worker and dispatcher terminals. Check both for startup errors.
echo Each new supported upload is automatically dispatched when eligible.
echo A failed ingestion can remain QUEUED/FAILED and requires investigation;
echo this launcher does not mark documents READY without verified publication.
exit /b 0
