@echo off
setlocal DisableDelayedExpansion
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_DRYRUN="
if not exist "%~dp0runtime\runtime.json" goto external
set "AEGIS_PYTHON312=%~dp0runtime\python\python.exe"
if not exist "%AEGIS_PYTHON312%" goto incomplete
goto configured

:external
if defined AEGIS_PYTHON312 goto configured
where.exe py.exe >nul 2>&1
if errorlevel 1 goto missing
if not "%~1"=="" goto use_py
where.exe pyw.exe >nul 2>&1
if errorlevel 1 goto use_py
start "" pyw.exe -3.12 -X utf8 -I -B "%~dp0scripts\start_windows_release.py" --background
exit /b %ERRORLEVEL%

:use_py
py.exe -3.12 -X utf8 -I -B "%~dp0scripts\start_windows_release.py" %*
goto complete

:configured
if not exist "%AEGIS_PYTHON312%" goto missing
if not "%~1"=="" goto use_configured
for %%P in ("%AEGIS_PYTHON312%") do set "AEGIS_PYTHONW=%%~dpPpythonw.exe"
if not exist "%AEGIS_PYTHONW%" goto use_configured
start "" "%AEGIS_PYTHONW%" -X utf8 -I -B "%~dp0scripts\start_windows_release.py" --background
exit /b %ERRORLEVEL%

:use_configured
"%AEGIS_PYTHON312%" -X utf8 -I -B "%~dp0scripts\start_windows_release.py" %*
goto complete

:incomplete
echo Aegis runtime files are missing. Extract the package again and run Aegis.cmd --check.
pause
exit /b 1

:missing
echo A prepared Windows x64 Python 3.12 environment is required.
echo Set AEGIS_PYTHON312 to the full path of its python.exe. See README.ko.md.
pause
exit /b 1

:complete
set "AEGIS_EXIT_CODE=%ERRORLEVEL%"
if not "%AEGIS_EXIT_CODE%"=="0" pause
exit /b %AEGIS_EXIT_CODE%
