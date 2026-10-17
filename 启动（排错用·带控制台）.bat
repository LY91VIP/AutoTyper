@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 排错模式：会保留控制台窗口，方便看报错。
if exist "%~dp0打字助手.exe" (
  "%~dp0打字助手.exe" --settings --debug
) else (
  "%~dp0..\..\.venv\Scripts\python.exe" "%~dp0AutoTyper.pyw" --settings --debug
)
echo.
echo 程序已退出，错误码：%errorlevel%
pause
