@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\robot"
echo Thu goi agy de viet tom tat tieng Viet...
python cupcake_robot.py --mode local --test-llm
echo.
pause
