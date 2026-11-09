# 本地后端启动脚本：脱离代理会话生命周期（供 Windows 计划任务调用）
# 用法：schtasks /Create /TN HealthAI-UV /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File <本文件路径>" ...
Set-Location (Split-Path $PSScriptRoot -Parent -Parent)  # 项目根目录
& "D:\conda-envs\health_ai_env\python.exe" -u -m uvicorn web_api:app --host 0.0.0.0 --port 8000
