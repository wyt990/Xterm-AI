import webview
import threading
import os
import sys
import time
import socket
import uvicorn
import multiprocessing
import shutil

# 允许 PyInstaller 打包时正确处理多进程
if __name__ == '__main__':
    multiprocessing.freeze_support()

def find_free_port():
    """动态寻找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def start_backend(port):
    """在后台线程运行 FastAPI"""
    from backend.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")

def init_environment(app_root, bundle_path):
    """
    初始化环境：确保 .env 和数据库存在
    app_root: 用户数据持久化目录 (可执行文件所在目录)
    bundle_path: 资源包目录 (PyInstaller 解压后的临时目录)
    """
    # 1. 检查并初始化 .env
    env_path = os.path.join(app_root, ".env")
    if not os.path.exists(env_path):
        env_example = os.path.join(bundle_path, ".env.example")
        if os.path.exists(env_example):
            print(f"📦 正在初始化配置文件: {env_path}")
            shutil.copy2(env_example, env_path)
    
    # 2. 检查并初始化数据库
    # 注意：这里我们强制将数据库放在 app_root/config 目录下以实现持久化
    db_dir = os.path.join(app_root, "config")
    db_path = os.path.join(db_dir, "xterm.db")
    if not os.path.exists(db_path):
        os.makedirs(db_dir, exist_ok=True)
        db_example = os.path.join(bundle_path, "config", "xterm.db.example")
        if os.path.exists(db_example):
            print(f"📦 正在根据模板初始化数据库: {db_path}")
            shutil.copy2(db_example, db_path)
    
    return env_path, db_path

if __name__ == '__main__':
    # 确定两个关键路径
    if hasattr(sys, '_MEIPASS'):
        # 打包后的环境
        bundle_path = sys._MEIPASS
        # 在打包模式下，用户数据应存放在可执行文件所在目录，而不是临时解压目录
        app_root = os.path.dirname(sys.executable)
    else:
        # 开发环境
        bundle_path = os.path.abspath(".")
        app_root = bundle_path

    # 初始化环境并获取持久化路径
    env_path, db_path = init_environment(app_root, bundle_path)

    # 自动分配端口并注入环境变量
    port = find_free_port()
    os.environ["SERVER_PORT"] = str(port)
    os.environ["DB_PATH"] = db_path
    
    print(f"🚀 正在启动后端服务，端口: {port}...")
    
    # 启动后端线程
    t = threading.Thread(target=start_backend, args=(port,), daemon=True)
    t.start()
    
    # 等待服务就绪
    time.sleep(1.5)
    
    # 创建桌面窗口
    print("🖥️ 正在创建桌面窗口...")
    window = webview.create_window(
        'XTerm-AI 智能运维终端', 
        f'http://127.0.0.1:{port}/frontend/index.html',
        width=1280,
        height=800,
        min_size=(1024, 768)
    )
    
    # 启动 GUI
    webview.start()
