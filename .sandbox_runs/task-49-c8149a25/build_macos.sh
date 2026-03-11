#!/bin/bash
# XTerm-AI macOS 打包脚本

# 读取版本号
VERSION=$(cat VERSION)
echo "📦 正在准备 macOS 打包环境 (版本: v$VERSION)..."

# 1. 安装打包工具 (如果尚未安装)
pip3 install pyinstaller pywebview uvicorn fastapi

# 2. 清理旧数据
rm -rf build/ dist/macOS/*.app dist/macOS/*.zip

# 3. 执行 PyInstaller 打包
# --add-data "源路径:目标路径"
# 注意: macOS 下使用冒号 : 作为路径分隔符
echo "🏗️ 正在使用 PyInstaller 编译项目..."
pyinstaller --noconsole --onefile \
    --icon "static/terminal.png" \
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

# 4. 移动结果并压缩
mkdir -p dist/macOS
mv dist/XTerm-AI-macOS dist/macOS/
cd dist/macOS
zip -r XTerm-AI-macOS-v$VERSION.zip XTerm-AI-macOS
cd ../..

echo "✅ macOS 打包完成！文件位置: dist/macOS/XTerm-AI-macOS-v$VERSION.zip"
