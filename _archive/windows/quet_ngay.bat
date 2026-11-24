@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0..\robot"
if not exist "..\logs" mkdir "..\logs"
echo Dang quet cac nguon, vui long doi...
python cupcake_robot.py --mode local >> "..\logs\robot.log" 2>&1
echo.
echo Xong. Xem ket qua o file logs\robot.log hoac mo trang web.
pause
