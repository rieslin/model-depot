@echo off
rem Start Model Depot in the background and open its page in the browser. Double-click to run.
rem   - If it is already running, only the page is opened.
rem   - On the first start it looks for a Python (3.10 or newer) that has aiohttp and Pillow. If none has them it
rem     asks before downloading anything; if there is no Python at all it lets you pick one.
rem   - If it cannot start, a message says why; the full log is in data\logs.
rem To stop it: Stop.cmd
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start.ps1" %*
