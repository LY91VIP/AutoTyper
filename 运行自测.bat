@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0打字助手.exe" (
  "%~dp0打字助手.exe" --selftest --with-windows
) else (
  "%~dp0..\..\.venv\Scripts\python.exe" "%~dp0AutoTyper.pyw" --selftest --with-windows
)
set code=%errorlevel%
echo.
if %code% neq 0 (echo 自测有失败项，错误码：%code%) else (echo 自测全部通过)
pause
exit /b %code%
