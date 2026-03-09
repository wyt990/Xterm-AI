# XTerm-AI PowerShell 打包脚本
# 设置 UTF-8 编码，避免中文乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001 | Out-Null

$VERSION = Get-Content -Path VERSION -TotalCount 1
Write-Host "📦 正在准备 Windows 打包环境 (版本: v$VERSION)..." -ForegroundColor Cyan

# 1. 卸载冲突的 jwt 包（python-jwt 与 PyJWT 冲突，需用 PyJWT）
pip uninstall jwt -y 2>$null
& pip install -r requirements.txt pyinstaller pywebview

# 2. 清理旧数据
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

# 3. 执行 PyInstaller 打包 (Windows 分号分隔符)
Write-Host "🏗️ 正在使用 PyInstaller 编译项目..." -ForegroundColor Yellow
& pyinstaller --noconsole --onefile `
    --name "XTerm-AI-Windows" `
    --add-data "frontend;frontend" `
    --add-data "static;static" `
    --add-data "config;config" `
    --add-data "backend;backend" `
    --add-data ".env.example;.env.example" `
    --collect-all "dotenv" `
    --collect-all "httpx" `
    --hidden-import "dotenv" `
    --hidden-import "jwt" `
    --hidden-import "multipart" `
    --hidden-import "sqlite3" `
    --hidden-import "_sqlite3" `
    --hidden-import "paramiko" `
    --hidden-import "httpx" `
    --hidden-import "cryptography" `
    --hidden-import "cryptography.fernet" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.protocols" `
    --hidden-import "uvicorn.loops" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols.http" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "uvicorn.lifespan.off" `
    main_desktop.py

# 4. 打包压缩包
Write-Host "📑 正在压缩结果..." -ForegroundColor Green
Compress-Archive -Path dist/XTerm-AI-Windows.exe -DestinationPath dist/XTerm-AI-Windows-v$VERSION.zip -Force

Write-Host "✅ Windows 打包完成！文件位置: dist/XTerm-AI-Windows-v$VERSION.zip" -ForegroundColor Green
