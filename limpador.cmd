@echo off
where py >nul 2>nul
if errorlevel 1 goto sempy
py -3 "%~dp0main.py" %*
exit /b %errorlevel%
:sempy
python "%~dp0main.py" %*
exit /b %errorlevel%
