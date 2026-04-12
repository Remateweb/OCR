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
echo [1/6] Verificando Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo    Python nao encontrado. Instalando via winget...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo    ERRO: Nao foi possivel instalar Python automaticamente.
        echo    Instale manualmente: https://www.python.org/downloads/
        echo    IMPORTANTE: Marque "Add python.exe to PATH"
        echo.
        pause
        exit /b 1
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
)
python --version
echo    Python OK!
echo.

:: -----------------------------------------------
:: 2. Verificar FFmpeg
:: -----------------------------------------------
echo [2/6] Verificando FFmpeg...
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo    FFmpeg nao encontrado. Instalando via winget...
    winget install Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo    Tentando download direto...
        powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TEMP%\ffmpeg.zip'; Expand-Archive '%TEMP%\ffmpeg.zip' -DestinationPath 'C:\ffmpeg' -Force }"
        for /d %%D in (C:\ffmpeg\ffmpeg-*) do set "FFMPEG_BIN=%%D\bin"
        if defined FFMPEG_BIN (
            setx PATH "%PATH%;%FFMPEG_BIN%" /M 2>nul || setx PATH "%PATH%;%FFMPEG_BIN%"
            set "PATH=%PATH%;%FFMPEG_BIN%"
        )
    )
)
ffmpeg -version 2>nul | findstr "version" && echo    FFmpeg OK! || echo    FFmpeg sera configurado apos reiniciar.
echo.

:: -----------------------------------------------
:: 3. Configurar projeto
:: -----------------------------------------------
echo [3/6] Configurando ambiente Python...
cd /d "%~dp0"

if not exist "venv" (
    echo    Criando ambiente virtual...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo    Instalando dependencias base...
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install pystray -q

echo    Dependencias base OK!
echo.

:: -----------------------------------------------
:: 4. Detectar GPU NVIDIA e instalar PyTorch CUDA
:: -----------------------------------------------
echo [4/6] Detectando GPU NVIDIA...
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo    GPU NVIDIA nao detectada. Usando modo CPU.
    echo    OCR vai funcionar, mas mais lento.
) else (
    echo    GPU NVIDIA detectada!
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>nul

    echo.
    echo    Instalando PyTorch com suporte CUDA (pode demorar ~2GB)...
    pip uninstall torch torchvision -y -q 2>nul
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128 -q

    echo    Verificando CUDA...
    python -c "import torch; gpu=torch.cuda.is_available(); name=torch.cuda.get_device_name(0) if gpu else 'N/A'; print(f'    CUDA: {gpu} | GPU: {name}')"
    if errorlevel 1 (
        echo    AVISO: CUDA nao funcionou. Usando CPU como fallback.
    )
)
echo.

:: -----------------------------------------------
:: 5. Criar diretorios
:: -----------------------------------------------
echo [5/6] Criando diretorios...
if not exist "data" mkdir data
if not exist "frames" mkdir frames
echo    Diretorios OK!
echo.

:: -----------------------------------------------
:: 6. Criar atalhos e scripts de inicializacao
:: -----------------------------------------------
echo [6/6] Criando atalhos...

set "SCRIPT_DIR=%~dp0"
set "DESKTOP=%USERPROFILE%\Desktop"

:: Script de inicializacao (modo tray)
(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo call venv\Scripts\activate.bat
echo start /b python tray_service.py
echo exit
) > "%SCRIPT_DIR%start_ocr.bat"

:: Script de inicializacao (modo console - para debug)
(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo call venv\Scripts\activate.bat
echo echo Iniciando OCR RemateWeb...
echo echo Acesse: http://localhost:8080
echo echo.
echo python -m uvicorn server:app --host 0.0.0.0 --port 8080
echo pause
) > "%SCRIPT_DIR%start_console.bat"

:: Atalho no Desktop (tray)
powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\OCR RemateWeb.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_ocr.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'OCR RemateWeb - Extracao de dados de leilao'; $s.WindowStyle = 7; $s.Save() }"

:: Atalho no Desktop (console)
powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\OCR Console.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_console.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'OCR RemateWeb - Modo Console (debug)'; $s.Save() }"

:: Adicionar ao Startup (iniciar com Windows)
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
powershell -Command "& { $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP%\OCR RemateWeb.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_ocr.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.WindowStyle = 7; $s.Save() }"

echo    Atalhos criados no Desktop!
echo    Adicionado ao Startup do Windows (inicia automaticamente)!
echo.

echo ============================================
echo    Setup concluido com sucesso!
echo.
echo    Atalhos criados:
echo      - "OCR RemateWeb" (bandeja do sistema)
echo      - "OCR Console"   (modo debug)
echo.
echo    O servico inicia automaticamente com o
echo    Windows (via Startup).
echo.
echo    Acesse: http://localhost:8080
echo ============================================
echo.

:: Perguntar se quer iniciar agora
set /p INICIAR="Deseja iniciar agora? (S/N): "
if /i "%INICIAR%"=="S" (
    echo Iniciando...
    call "%SCRIPT_DIR%start_console.bat"
)

pause
