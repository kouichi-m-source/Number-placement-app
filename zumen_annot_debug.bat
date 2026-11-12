@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 "zumen_annot.pyw") || (python "zumen_annot.pyw")
if errorlevel 1 pause
