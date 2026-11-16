@echo off
chcp 65001 >nul
cd /d "%~dp0"
FGDocQA.exe %*
if errorlevel 1 pause
