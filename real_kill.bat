@echo off
echo [*] Killing all AgentX processes...
taskkill /F /IM python.exe /T 2>nul
taskkill /F /IM node.exe /T 2>nul
echo [*] Done.
