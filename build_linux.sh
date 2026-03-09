#!/bin/bash
# XTerm-AI Linux 打包脚本

# 读取版本号
VERSION=$(cat VERSION)
echo "📦 正在准备 Linux 打包环境 (版本: v$VERSION)..."

# 1. 检查并安装打包工具
pip install pyinstaller pywebview uvicorn fastapi

# 2. 清理旧数据
rm -rf build/ dist/linux/*.run dist/linux/*.zip

# 3. 执行 PyInstaller 打包命令
# --add-data "源路径:目标路径"
# 注意: Linux 下使用冒号 : 作为路径分隔符
echo "🏗️ 正在使用 PyInstaller 编译项目..."
pyinstaller --noconsole --onefile \
    --name "XTerm-AI-Linux" \
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
mkdir -p dist/linux
mv dist/XTerm-AI-Linux dist/linux/
cd dist/linux
zip -r XTerm-AI-Linux-v$VERSION.zip XTerm-AI-Linux
cd ../..

echo "✅ Linux 打包完成！文件位置: dist/linux/XTerm-AI-Linux-v$VERSION.zip"
