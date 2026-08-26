@echo off
echo ========================================
echo  Forex Scalper Bot - Installation
echo ========================================
echo.

echo [1/3] Checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found!
    echo Please install Python 3.9+ from https://www.python.org/
    pause
    exit /b 1
)

echo.
echo [2/3] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo.
echo [3/3] Setting up configuration...
if not exist .env (
    copy .env.example .env
    echo .env file created. Please edit it with your MT5 credentials.
) else (
    echo .env file already exists.
)

echo.
echo ========================================
echo  Installation Complete!
echo ========================================
echo.
echo Next steps:
echo 1. Edit .env with your MT5 demo account details
echo 2. Run: python setup_and_test.py
echo 3. Run: python main.py
echo.
echo Read START_HERE.md for detailed instructions.
echo.
pause
