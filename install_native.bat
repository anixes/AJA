@echo off
echo Building AgentX Native Rust Layer...

cd packages\agentx-native

echo Installing Maturin build system...
python -m pip install maturin

echo Building and installing wheel for current environment...
maturin build --release
for /f "delims=" %%i in ('dir /b /s target\wheels\*.whl') do python -m pip install "%%i" --force-reinstall

cd ..\..
echo Native layer installed successfully!
