@echo off
REM Run ChatGPT Desktop in TRUE PORTABLE mode
REM Data sẽ lưu cạnh exe trong folder ./data/, không vào %APPDATA%
REM
REM Cách dùng: double-click file này (KHÔNG chạy thẳng chatgpt.exe)

setlocal
set "ROOT=%~dp0"
set "APPDATA=%ROOT%data"
set "LOCALAPPDATA=%ROOT%data"

if not exist "%ROOT%data\com.nofwl.chatgpt" mkdir "%ROOT%data\com.nofwl.chatgpt"

echo ==================================================
echo  ChatGPT Desktop — Portable Mode
echo  Data location: %ROOT%data\com.nofwl.chatgpt
echo ==================================================
echo.

start "" "%ROOT%chatgpt.exe"
