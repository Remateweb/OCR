@echo off
REM ============================================================
REM Setup Script - OCR RemateWeb (Windows com GPU)
REM ============================================================
REM Pre-requisitos:
REM   1. Python 3.11+ instalado (python.org, marcar "Add to PATH")
REM   2. Git instalado (git-scm.com)
REM   3. FFmpeg no PATH (gyan.dev/ffmpeg/builds)
REM   4. Driver NVIDIA atualizado
REM ============================================================

echo.
echo ============================================
echo    OCR RemateWeb - Setup Windows
echo ============================================
echo.

REM Verificar Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Python nao encontrado! Instale em python.org
    echo        Marque "Add Python to PATH" durante instalacao.
    pause
    exit /b 1
)

REM Verificar Git
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERRO] Git nao encontrado! Instale em git-scm.com
    pause
    exit /b 1
)

REM Verificar FFmpeg
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo [AVISO] FFmpeg nao encontrado no PATH!
    echo         Baixe em: https://www.gyan.dev/ffmpeg/builds/
    echo         Extraia e adicione a pasta bin ao PATH do Windows.
    pause
    exit /b 1
)

echo [1/4] Pre-requisitos OK!

REM Criar ambiente virtual
echo [2/4] Criando ambiente virtual Python...
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate.bat

REM Instalar PyTorch com CUDA
echo [3/4] Instalando dependencias (pode demorar 5-10 min)...
pip install --upgrade pip -q
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124 -q
pip install -r requirements.txt -q

REM Criar diretorios
echo [4/4] Criando diretorios...
if not exist "data" mkdir data
if not exist "frames" mkdir frames
if not exist "output" mkdir output

echo.
echo ============================================
echo    Setup concluido!
echo.
echo    Para iniciar, rode:
echo    start_windows.bat
echo ============================================
echo.
pause
