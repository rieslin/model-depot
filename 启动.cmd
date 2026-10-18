@echo off
rem The same as Start.cmd, under a Chinese name. (Chinese text is kept out of this file: cmd mangles UTF-8.)
rem Starts Model Depot in the background and opens its page in the browser. See Start.cmd for what it does.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start.ps1" %*
