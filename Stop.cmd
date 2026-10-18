@echo off
rem Stop Model Depot. Double-click to run.
rem   - Fingerprints already computed are kept; a file that was half-way through is done again next time.
rem   - Model files and the files next to them are never touched by stopping.
rem   - If it was not running, a message says so and nothing else happens.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\stop.ps1" %*
