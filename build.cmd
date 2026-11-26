@echo off
rem Avvia build.ps1 aggirando la policy di esecuzione (solo per questo processo).
rem Fai doppio clic su questo file. Argomenti extra (es. -SkipTests) vengono inoltrati.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 pause
