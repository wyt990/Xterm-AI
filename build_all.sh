#!/bin/bash
# XTerm-AI 一键全平台打包引导脚本

# 读取版本号
VERSION=$(cat VERSION)
echo "🚀 XTerm-AI 跨平台桌面客户端构建向导 (版本: v$VERSION)"
echo "------------------------------------------------"
echo "由于 PyInstaller 本身不支持跨平台交叉编译 (Cross-Compilation)，"
echo "打包工作需要分别在 Linux、Windows 和 macOS 环境下执行。"

# 1. 检查当前系统
OS_TYPE=$(uname -s)

case "$OS_TYPE" in
    Linux*)
        echo "检测到系统: Linux"
        echo "正在为您运行 Linux 打包脚本 (build_linux.sh)..."
        chmod +x build_linux.sh
        ./build_linux.sh
        ;;
    Darwin*)
        echo "检测到系统: macOS"
        echo "正在为您运行 macOS 打包流程..."
        # 类似 Linux 的脚本，但输出后缀为 .app
        pip install pyinstaller pywebview uvicorn fastapi
        pyinstaller --noconsole --onefile \
            --name "XTerm-AI-macOS" \
            --add-data "frontend:frontend" \
            --add-data "static:static" \
            --add-data "config:config" \
            --add-data "backend:backend" \
            --hidden-import "uvicorn.logging" \
            --hidden-import "uvicorn.protocols" \
            --hidden-import "uvicorn.loops" \
            --hidden-import "uvicorn.loops.auto" \
            --hidden-import "uvicorn.protocols.http" \
            --hidden-import "uvicorn.protocols.http.auto" \
            --hidden-import "uvicorn.protocols.websockets" \
            --hidden-import "uvicorn.protocols.websockets.auto" \
            --hidden-import "uvicorn.lifespan" \
            --hidden-import "uvicorn.lifespan.on" \
            --hidden-import "uvicorn.lifespan.off" \
            main_desktop.py
        
        # 压缩结果
        mkdir -p dist/macOS
        mv dist/XTerm-AI-macOS dist/macOS/
        cd dist/macOS
        zip -r XTerm-AI-macOS-v$VERSION.zip XTerm-AI-macOS
        cd ../..
        echo "✅ macOS 打包完成！文件位置: dist/macOS/XTerm-AI-macOS-v$VERSION.zip"
        ;;
    *)
        echo "检测到系统: 其他 / Windows"
        echo "请在 Windows CMD 或 PowerShell 下直接运行以下任一文件："
        echo "1. build.bat  (CMD)"
        echo "2. build.ps1  (PowerShell)"
        ;;
esac

echo "------------------------------------------------"
echo "🎉 打包任务处理完毕。"
