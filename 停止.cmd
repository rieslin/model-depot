@echo off
rem The same as Stop.cmd, under a Chinese name. (Chinese text is kept out of this file: cmd mangles UTF-8.)
rem Stops Model Depot. See Stop.cmd for what it does.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\stop.ps1" %*
