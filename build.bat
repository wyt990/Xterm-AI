@echo off
REM XTerm-AI Windows 打包脚本 (CMD)

set /p VERSION=<VERSION
echo 📦 正在准备 Windows 打包环境 (版本: v%VERSION%)...

REM 1. 安装依赖
pip install pyinstaller pywebview uvicorn fastapi

REM 2. 清理旧数据
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM 3. 执行 PyInstaller 打包
REM 注意: Windows 下使用分号 ; 作为路径分隔符
echo 🏗️ 正在使用 PyInstaller 编译项目...
pyinstaller --noconsole --onefile ^
    --name "XTerm-AI-Windows" ^
    --add-data "frontend;frontend" ^
    --add-data "static;static" ^
    --add-data "config;config" ^
    --add-data "backend;backend" ^
    --hidden-import "uvicorn.logging" ^
    --hidden-import "uvicorn.protocols" ^
    --hidden-import "uvicorn.loops" ^
    --hidden-import "uvicorn.loops.auto" ^
    --hidden-import "uvicorn.protocols.http" ^
    --hidden-import "uvicorn.protocols.http.auto" ^
    --hidden-import "uvicorn.protocols.websockets" ^
    --hidden-import "uvicorn.protocols.websockets.auto" ^
    --hidden-import "uvicorn.lifespan" ^
    --hidden-import "uvicorn.lifespan.on" ^
    --hidden-import "uvicorn.lifespan.off" ^
    main_desktop.py

REM 4. 打包为 ZIP 压缩包 (Windows 10+ 包含 powershell 压缩)
powershell Compress-Archive -Path dist/XTerm-AI-Windows.exe -DestinationPath dist/XTerm-AI-Windows-v%VERSION%.zip -Force

echo ✅ Windows 打包完成！文件位置: dist/XTerm-AI-Windows-v%VERSION%.zip
pause
