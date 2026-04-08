@echo off
REM ============================================================
REM Start Script - OCR RemateWeb (Windows)
REM ============================================================
REM Duplo-clique para iniciar o servidor OCR
REM ============================================================

echo.
echo ============================================
echo    OCR RemateWeb - Iniciando...
echo ============================================
echo.

call venv\Scripts\activate.bat

echo Servidor rodando em: http://localhost:8000
echo Para acessar de outro PC na rede: http://SEU_IP:8000
echo.
echo Pressione Ctrl+C para parar.
echo.

uvicorn server:app --host 0.0.0.0 --port 8000
