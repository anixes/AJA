@echo off
setlocal enabledelayedexpansion

:: =======================================================
:: AJA Native Core Installer (Persistence Mode)
:: =======================================================

echo [*] Detecting project structure...
set "ROOT=%CD%"
set "NATIVE_DIR=%ROOT%\packages\aja-native"

if not exist "%NATIVE_DIR%" (
    echo [ERROR] Native core directory not found: %NATIVE_DIR%
    pause
    exit /b 1
)

echo [*] Detecting Python environment...
set "PYTHON_EXE=python"
where %PYTHON_EXE% >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

echo [*] Verifying Maturin...
%PYTHON_EXE% -m maturin --version >nul 2>nul
if %errorlevel% neq 0 (
    echo [*] Maturin not found. Installing...
    %PYTHON_EXE% -m pip install maturin
)

echo [*] Cleaning previous builds...
if exist "target" (
    echo [*] Removing target directory to ensure clean slate...
    rmdir /s /q target
)

echo [*] Building AJA Native Core (RELEASE)...
cd /d "%NATIVE_DIR%"

:: Build the wheel in release mode.
:: --strip removes debug symbols (the 1GB PDB file) to save massive memory.
%PYTHON_EXE% -m maturin build --release --strip

if %errorlevel% equ 0 (
    echo [*] Installing the optimized wheel...
    :: Find the generated .whl file in target/wheels
    for /f "tokens=*" %%i in ('dir /b target\wheels\*.whl') do (
        set "WHEEL=target\wheels\%%i"
    )
    %PYTHON_EXE% -m pip install --force-reinstall "!WHEEL!"
    
    echo.
    echo [SUCCESS] AJA Native Core is now installed in RELEASE mode.
    echo [SUCCESS] DLL size reduced and debug symbols stripped.
) else (
    echo.
    echo [ERROR] Native build failed. Check your Rust/MSVC toolchain.
)

pause
