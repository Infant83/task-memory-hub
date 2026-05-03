@echo off
setlocal
cd /d "%~dp0.."
python -m task_memory_hub.mcp_server
