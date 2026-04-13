@echo off
chcp 65001 >nul
title OCR RemateWeb - Instalacao
color 0A

echo.
echo ============================================
echo    OCR RemateWeb - Setup Windows
echo ============================================
echo.

:: Salvar diretorio do script
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: -----------------------------------------------
:: 1. Verificar Python
:: -----------------------------------------------
echo [1/6] Verificando Python...
where python >nul 2>&1
if errorlevel 1 (
    echo    Python nao encontrado. Instalando via winget...
    winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo    ERRO: Instale Python manualmente: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
)
python --version
echo    OK!
echo.

:: -----------------------------------------------
:: 2. Verificar FFmpeg
:: -----------------------------------------------
echo [2/6] Verificando FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo    FFmpeg nao encontrado. Instalando via winget...
    winget install Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements 2>nul
    echo    Verifique se foi instalado apos reiniciar o terminal.
) else (
    echo    OK!
)
echo.

:: -----------------------------------------------
:: 3. Criar venv e instalar deps
:: -----------------------------------------------
echo [3/6] Configurando ambiente Python...
cd /d "%SCRIPT_DIR%"

if not exist "venv\Scripts\python.exe" (
    echo    Criando ambiente virtual...
    python -m venv venv
)

echo    Ativando venv...
call "%SCRIPT_DIR%venv\Scripts\activate.bat"

echo    Instalando dependencias...
"%SCRIPT_DIR%venv\Scripts\pip.exe" install --upgrade pip -q
"%SCRIPT_DIR%venv\Scripts\pip.exe" install -r "%SCRIPT_DIR%requirements.txt" -q
"%SCRIPT_DIR%venv\Scripts\pip.exe" install pystray -q

echo    OK!
echo.

:: -----------------------------------------------
:: 4. Detectar GPU NVIDIA
:: -----------------------------------------------
echo [4/6] Detectando GPU NVIDIA...
where nvidia-smi >nul 2>&1
if errorlevel 1 goto NO_GPU

nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
echo.
echo    Instalando PyTorch com CUDA...
"%SCRIPT_DIR%venv\Scripts\pip.exe" uninstall torch torchvision -y -q 2>nul
"%SCRIPT_DIR%venv\Scripts\pip.exe" install torch torchvision --index-url https://download.pytorch.org/whl/cu128
echo    Verificando CUDA...
"%SCRIPT_DIR%venv\Scripts\python.exe" -c "import torch; print('    CUDA:', torch.cuda.is_available())"
goto GPU_DONE

:NO_GPU
echo    GPU NVIDIA nao detectada. Usando modo CPU.

:GPU_DONE
echo.

:: -----------------------------------------------
:: 5. Criar diretorios
:: -----------------------------------------------
echo [5/6] Criando diretorios...
cd /d "%SCRIPT_DIR%"
if not exist "data" mkdir data
if not exist "frames" mkdir frames
echo    OK!
echo.

:: -----------------------------------------------
:: 6. Criar atalhos
:: -----------------------------------------------
echo [6/6] Criando atalhos...

set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

:: start_ocr.bat
echo @echo off > "%SCRIPT_DIR%start_ocr.bat"
echo cd /d "%SCRIPT_DIR%" >> "%SCRIPT_DIR%start_ocr.bat"
echo call "%SCRIPT_DIR%venv\Scripts\activate.bat" >> "%SCRIPT_DIR%start_ocr.bat"
echo start "" /b "%SCRIPT_DIR%venv\Scripts\pythonw.exe" "%SCRIPT_DIR%tray_service.py" >> "%SCRIPT_DIR%start_ocr.bat"

:: start_console.bat
echo @echo off > "%SCRIPT_DIR%start_console.bat"
echo cd /d "%SCRIPT_DIR%" >> "%SCRIPT_DIR%start_console.bat"
echo call "%SCRIPT_DIR%venv\Scripts\activate.bat" >> "%SCRIPT_DIR%start_console.bat"
echo echo OCR RemateWeb - Modo Console >> "%SCRIPT_DIR%start_console.bat"
echo echo Acesse: http://localhost:8080 >> "%SCRIPT_DIR%start_console.bat"
echo echo. >> "%SCRIPT_DIR%start_console.bat"
echo "%SCRIPT_DIR%venv\Scripts\python.exe" -m uvicorn server:app --host 0.0.0.0 --port 8080 >> "%SCRIPT_DIR%start_console.bat"
echo pause >> "%SCRIPT_DIR%start_console.bat"

:: Atalho Desktop - Tray
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\OCR RemateWeb.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_ocr.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'OCR RemateWeb'; $s.WindowStyle = 7; $s.Save()"

:: Atalho Desktop - Console
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DESKTOP%\OCR Console.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_console.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.Description = 'OCR Console'; $s.Save()"

:: Startup
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP%\OCR RemateWeb.lnk'); $s.TargetPath = '%SCRIPT_DIR%start_ocr.bat'; $s.WorkingDirectory = '%SCRIPT_DIR%'; $s.WindowStyle = 7; $s.Save()"

echo    Atalhos criados!
echo.

echo ============================================
echo    Setup concluido!
echo.
echo    Desktop:
echo      - OCR RemateWeb (icone bandeja)
echo      - OCR Console (modo debug)
echo.
echo    Inicia automaticamente com Windows.
echo    Acesse: http://localhost:8080
echo ============================================
echo.

set /p INICIAR="Iniciar agora? (S/N): "
if /i "%INICIAR%"=="S" (
    start "" "%SCRIPT_DIR%start_console.bat"
)

pause
