@echo off
rem Opens the Vital Sign control window with this folder's venv, without a console window.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0gui.py"
