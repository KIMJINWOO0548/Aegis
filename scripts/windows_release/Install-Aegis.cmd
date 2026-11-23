@echo off
setlocal DisableDelayedExpansion
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYLAUNCHER_DRYRUN="
set "AEGIS_INSTALL_PY=%~dp0runtime\python\python.exe"
if exist "%AEGIS_INSTALL_PY%" goto run
if defined AEGIS_PYTHON312 set "AEGIS_INSTALL_PY=%AEGIS_PYTHON312%"
if exist "%AEGIS_INSTALL_PY%" goto run
where.exe py.exe >nul 2>&1
if errorlevel 1 goto missing
if "%~1"=="" goto initial_external
py.exe -3.12 -X utf8 -I -B "%~dp0scripts\manage_install.py" %*
exit /b %ERRORLEVEL%
:initial_external
py.exe -3.12 -X utf8 -I -B "%~dp0scripts\manage_install.py" install
set "AEGIS_INSTALL_EXIT=%ERRORLEVEL%"
pause
exit /b %AEGIS_INSTALL_EXIT%
:run
if "%~1"=="" goto initial
"%AEGIS_INSTALL_PY%" -X utf8 -I -B "%~dp0scripts\manage_install.py" %*
exit /b %ERRORLEVEL%
:initial
"%AEGIS_INSTALL_PY%" -X utf8 -I -B "%~dp0scripts\manage_install.py" install
set "AEGIS_INSTALL_EXIT=%ERRORLEVEL%"
pause
exit /b %AEGIS_INSTALL_EXIT%
:missing
echo Aegis setup needs the packaged Python runtime or a prepared Python 3.12 environment.
pause
exit /b 1
