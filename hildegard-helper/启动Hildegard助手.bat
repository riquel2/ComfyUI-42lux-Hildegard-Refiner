@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:/Users/rique/AppData/Local/Programs/Python/Python313/python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import tkinter,PIL,requests" >gui_start.log 2>&1
if errorlevel 1 goto DEPERR

"%PY%" -X utf8 gui.py >>gui_start.log 2>&1
if errorlevel 1 goto RUNERR
goto END

:DEPERR
echo.
echo [错误] 缺少依赖 tkinter / Pillow / requests，或 Python 路径不对。
echo        详情见同目录 gui_start.log
echo.
pause
goto END

:RUNERR
echo.
echo [错误] 程序启动失败，详情见同目录 gui_start.log
echo.
pause
:END