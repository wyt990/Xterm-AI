#!/bin/bash
# 检查 .env 是否存在
if [ ! -f .env ]; then
    echo "未找到 .env 文件，正在从 .env.example 复制..."
    cp .env.example .env
    echo "请先编辑 .env 文件配置您的基本参数！"
    exit 1
fi

# 从 .env 中加载变量，用于显示端口和数据库信息
source .env

# 检查数据库文件是否已初始化
if [ ! -f "$DB_PATH" ]; then
    echo "未发现本地数据库 $DB_PATH，尝试从模板复制..."
    
    # 查找模板文件：
    # 1. 直接按 .env 中的路径找同级 .example (若路径相对 backend 则是 ../config/xterm.db.example)
    # 2. 从项目根目录的 config 找
    TEMPLATE_DB="${DB_PATH}.example"
    
    if [ -f "$TEMPLATE_DB" ]; then
        mkdir -p $(dirname "$DB_PATH")
        cp "$TEMPLATE_DB" "$DB_PATH"
        echo "已根据模板初始化数据库：$DB_PATH"
    else
        echo "警告：未发现数据库模板 $TEMPLATE_DB，服务启动后将尝试自动创建空数据库。"
    fi
fi

echo "正在从 $DB_PATH 初始化数据库..."
mkdir -p $(dirname $DB_PATH)

echo "正在启动 XTerm-AI 服务 (端口 $SERVER_PORT)..."
cd backend
/root/miniconda3/envs/xterm/bin/python main.py
