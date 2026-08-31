@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

title PubMed 本地语义检索
echo ========================================
echo   PubMed 本地系统 - 一键启动
echo ========================================
echo.

REM ---- 选择 Python（优先 3.10+）----
set "PYEXE="
where py >nul 2>&1
if %errorlevel%==0 (
  for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
)
if not defined PYEXE (
  where python >nul 2>&1
  if %errorlevel%==0 (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
  )
)
if not defined PYEXE (
  if exist "C:\python313\python.exe" set "PYEXE=C:\python313\python.exe"
)
if not defined PYEXE (
  if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
)

if not defined PYEXE (
  echo [错误] 未找到 Python，请先安装 Python 3.10+ 并勾选 Add to PATH。
  pause
  exit /b 1
)

echo [信息] 使用 Python: %PYEXE%
"%PYEXE%" -c "import sys; raise SystemExit(0 if sys.version_info>=(3,10) else 1)" 2>nul
if errorlevel 1 (
  echo [错误] 需要 Python 3.10 或更高版本。
  "%PYEXE%" --version
  pause
  exit /b 1
)

REM ---- 修复 / 创建虚拟环境（系统重装后 Scripts\python.exe 仍可能存在但无法运行）----
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "NEED_VENV=0"
if not exist "%VENV_PY%" set "NEED_VENV=1"
if "%NEED_VENV%"=="0" (
  "%VENV_PY%" -c "import sys" >nul 2>&1
  if errorlevel 1 set "NEED_VENV=1"
)
if "%NEED_VENV%"=="1" (
  echo [信息] 虚拟环境缺失或损坏，正在重建 .venv ...
  if exist "%~dp0.venv" (
    echo [信息] 尝试就地升级 .venv ...
    "%PYEXE%" -m venv --upgrade --clear "%~dp0.venv"
    if errorlevel 1 (
      echo [信息] 升级失败，删除后重建 ...
      rmdir /s /q "%~dp0.venv" 2>nul
      "%PYEXE%" -m venv "%~dp0.venv"
    )
  ) else (
    "%PYEXE%" -m venv "%~dp0.venv"
  )
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [信息] 对齐当前 Python 解释器 ...
  "%PYEXE%" -m venv --upgrade "%~dp0.venv" >nul 2>&1
)

if not exist "%VENV_PY%" (
  echo [错误] 虚拟环境创建后仍找不到 python.exe
  pause
  exit /b 1
)

echo [信息] 检查并安装依赖...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>&1
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

REM ---- .env ----
if not exist "%~dp0.env" (
  if exist "%~dp0.env.example" (
    copy /y "%~dp0.env.example" "%~dp0.env" >nul
    echo [提示] 已从 .env.example 生成 .env，请填入 API Key。
  ) else (
    echo [警告] 未找到 .env，请自行创建并配置 API Key。
  )
)

echo.
echo [启动] http://127.0.0.1:5000
echo        关闭本窗口即停止服务
echo.

start "" "http://127.0.0.1:5000"
"%VENV_PY%" "%~dp0app.py"
set "ERR=%errorlevel%"
if not "%ERR%"=="0" (
  echo.
  echo [错误] 服务异常退出，代码 %ERR%
  pause
)
exit /b %ERR%
