@echo off
setlocal
cd /d "%~dp0"

rem Eerste start op deze computer: installeer de meegeleverde onderdelen (vendor) in runtime.
if not exist "runtime\ready.txt" (
    echo CinemaViewer wordt voor het eerst ingericht op deze computer...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1"
    if errorlevel 1 (
        echo.
        echo Installatie mislukt. Zie de melding hierboven.
        pause
        exit /b 1
    )
)

start "" "%~dp0runtime\python\pythonw.exe" "%~dp0CinemaViewer.pyw" %*
exit /b 0
