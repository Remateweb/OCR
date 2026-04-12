@echo off
chcp 65001 >nul
title OCR RemateWeb - Instalação
color 0A

echo.
echo ============================================
echo    OCR RemateWeb - Setup Windows
echo ============================================
echo.

:: -----------------------------------------------
:: 1. Verificar Python
:: -----------------------------------------------
echo [1/5] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo    Python não encontrado. Instalando via winget...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo    ERRO: Não foi possível instalar Python automaticamente.
        echo    Instale manualmente: https://www.python.org/downloads/
        echo    IMPORTANTE: Marque "Add python.exe to PATH"
        echo.
        pause
        exit /b 1
    )
    :: Refresh PATH
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
)
python --version
echo    Python OK!
echo.

:: -----------------------------------------------
:: 2. Verificar FFmpeg
:: -----------------------------------------------
echo [2/5] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo    FFmpeg não encontrado. Instalando via winget...
    winget install Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo    Tentando instalar FFmpeg manualmente...
        powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TEMP%\ffmpeg.zip'; Expand-Archive '%TEMP%\ffmpeg.zip' -DestinationPath 'C:\ffmpeg' -Force }"
        :: Find the bin folder and add to PATH
        for /d %%D in (C:\ffmpeg\ffmpeg-*) do set "FFMPEG_BIN=%%D\bin"
        if defined FFMPEG_BIN (
            setx PATH "%PATH%;%FFMPEG_BIN%" /M 2>nul || setx PATH "%PATH%;%FFMPEG_BIN%"
            set "PATH=%PATH%;%FFMPEG_BIN%"
            echo    FFmpeg instalado em %FFMPEG_BIN%
        )
    )
)
ffmpeg -version 2>nul | findstr "version" && echo    FFmpeg OK! || echo    FFmpeg será configurado após reiniciar.
echo.

:: -----------------------------------------------
:: 3. Configurar projeto
:: -----------------------------------------------
echo [3/5] Configurando projeto...
cd /d "%~dp0"

if not exist "venv" (
    echo    Criando ambiente virtual...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo    Instalando dependências (pode demorar)...
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install pystray Pillow -q

echo    Dependências OK!
echo.

:: -----------------------------------------------
:: 4. Criar diretórios
:: -----------------------------------------------
echo [4/5] Criando diretórios...
if not exist "data" mkdir data
if not exist "frames" mkdir frames
echo    Diretórios OK!
echo.

:: -----------------------------------------------
:: 5. Criar atalho na Área de Trabalho
:: -----------------------------------------------
echo [5/5] Criando atalho na Área de Trabalho...

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

:: Criar script de inicialização
(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo call venv\Scripts\activate.bat
echo start /b python tray_service.py
) > "%SCRIPT_DIR%start_ocr.bat"

:: Criar atalho com PowerShell
powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\OCR RemateWeb.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_ocr.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'OCR RemateWeb - Extração de dados de leilão'; $s.WindowStyle = 7; $s.Save() }"

echo    Atalho criado!
echo.

echo ============================================
echo    Setup concluído!
echo.
echo    Clique em "OCR RemateWeb" no Desktop
echo    para iniciar o serviço.
echo.
echo    O ícone aparecerá na bandeja do sistema
echo    (ao lado do relógio).
echo ============================================
echo.
pause
