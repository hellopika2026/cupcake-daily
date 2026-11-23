@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\robot"
if not exist "..\logs" mkdir "..\logs"
python cupcake_robot.py --mode local --skip-if-done-today >> "..\logs\robot.log" 2>&1
