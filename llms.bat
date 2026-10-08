@echo off
setlocal
rem Run from the repository directory, even when invoked from elsewhere.
pushd "%~dp0" || exit /b 1

if not exist "%USERPROFILE%\.llms" mkdir "%USERPROFILE%\.llms"
if errorlevel 1 goto :failed
for %%F in (providers-extra.json providers.json llms.json) do (
    copy /Y "llms\%%F" "%USERPROFILE%\.llms\%%F" >nul
    if errorlevel 1 goto :failed
)

if exist ".venv\Scripts\python.exe" (
    where uv >nul 2>&1
    if not errorlevel 1 (
        uv run -m llms %*
        goto :done
    )
    ".venv\Scripts\python.exe" -m llms %*
    goto :done
)

where python >nul 2>&1
if not errorlevel 1 (
    python -m llms %*
    goto :done
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -m llms %*
    goto :done
)
where python3 >nul 2>&1
if not errorlevel 1 (
    python3 -m llms %*
    goto :done
)
echo Python not found. Install Python 3.11 or later. >&2
goto :failed

:done
set "llms_exit_code=%errorlevel%"
popd
exit /b %llms_exit_code%

:failed
popd
exit /b 1
