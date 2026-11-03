@echo off
setlocal EnableExtensions
rem DocMind-AI Phase 3.9 + 3.10 consolidated OFFLINE acceptance gate.
rem Run from any CMD directory: tools\PHASE3_10_VERIFY.cmd
rem Uses only test suites, TypeScript, ESLint and build commands.
rem Excludes api.integration.test.ts because it writes to a live SQL database.
rem Does not modify pilot KBs, Qdrant, RustFS or run migrations.

cd /d "%~dp0.." || exit /b 1
for /f %%b in ('git branch --show-current') do set "CURRENT_BRANCH=%%b"
if /I not "%CURRENT_BRANCH%"=="feature/phase3-secure-retrieval" (
  echo FAIL: Expected feature/phase3-secure-retrieval; got %CURRENT_BRANCH%
  exit /b 1
)
echo.
echo ===== DocMind Phase 3.9 + 3.10 FINAL OFFLINE GATE =====
echo This script deliberately excludes database-writing API integration tests.
echo.
echo [1/6] Backend typecheck
pushd backend || exit /b 1
call npm run typecheck
if errorlevel 1 goto failed

echo [2/6] Backend production build
call npm run build
if errorlevel 1 goto failed

echo [3/6] Backend offline tests: authorization, retrieval, grounding, transport
call npx --no-install tsx --test ^
 tests/security.test.ts ^
 tests/answerApiSecurity.test.ts ^
 tests/answerDiagnosticVisibility.test.ts ^
 tests/databasePoolFailure.test.ts ^
 tests/evidenceConsistencyPhase37.test.ts ^
 tests/groundedAnswer.test.ts ^
 tests/groundedAnswerDimensions.test.ts ^
 tests/groundedAnswerFactualGrounding.test.ts ^
 tests/groundedAnswerPhase35.test.ts ^
 tests/groundedAnswerPhase36Adversarial.test.ts ^
 tests/groundedAnswerPhase38Cutoff.test.ts ^
 tests/groundedAnswerPhase38Duration.test.ts ^
 tests/groundedAnswerRobustness.test.ts ^
 tests/groundedAnswerStructured.test.ts ^
 tests/localMySqlAuth.test.ts ^
 tests/phase38AllCases.test.ts ^
 tests/phase39OllamaReliability.test.ts ^
 tests/retrievalApiSecurity.test.ts ^
 tests/retrievalBoundary.test.ts ^
 tests/retrievalPostFilter.test.ts ^
 tests/retrievalRanking.test.ts ^
 tests/transientDatabase.test.ts
if errorlevel 1 goto failed
popd

echo [4/6] Frontend lint
pushd frontend || exit /b 1
call npm run lint
if errorlevel 1 goto failed

echo [5/6] Frontend production build
call npm run build
if errorlevel 1 goto failed
popd

echo [6/6] Python worker offline unittest suite
pushd python-worker || exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo FAIL: python-worker virtualenv is missing.
  exit /b 1
)
".venv\Scripts\python.exe" -m unittest discover -s worker -t . -p "test_*.py"
if errorlevel 1 goto failed
popd

echo.
echo ===== OFFLINE ACCEPTANCE PASSED =====
echo Next: browser Phase 3.10 live gate and independent MySQL/Ollama checks.
exit /b 0

:failed
echo.
echo ===== OFFLINE ACCEPTANCE FAILED - DO NOT CLAIM PHASE 3 COMPLETE =====
exit /b 1
