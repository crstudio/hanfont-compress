@echo off
:: HanFont Compressor Launcher
:: Usage: run_hfc.bat --demo --route all --output report.html

setlocal
set PYTHONPATH=%~dp0src
python -m hfc.cli %*
endlocal