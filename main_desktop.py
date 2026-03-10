import webview
import threading
import os
import sys
import time
import socket
import uvicorn
import multiprocessing
import shutil
import tkinter as tk
if os.name == 'nt':
    import ctypes

# 允许 PyInstaller 打包时正确处理多进程
if __name__ == '__main__':
    multiprocessing.freeze_support()

def _log(msg):
    """安全输出：打包后 --noconsole 模式下 stdout 可能不可用，忽略输出错误"""
    try:
        print(msg)
    except (ValueError, OSError):
        pass


def _set_clipboard_text_tk(text):
    """跨平台兜底：使用 tkinter 写入剪贴板"""
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        value = text if isinstance(text, str) else str(text or "")
        root.clipboard_clear()
        root.clipboard_append(value)
        # update 确保剪贴板内容真正提交给系统
        root.update()
        return True
    except Exception:
        return False
    finally:
        try:
            if root:
                root.destroy()
        except Exception:
            pass


def _get_clipboard_text_tk():
    """跨平台兜底：使用 tkinter 读取剪贴板"""
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get()
        return text if isinstance(text, str) else ""
    except Exception:
        return ""
    finally:
        try:
            if root:
                root.destroy()
        except Exception:
            pass


if os.name == 'nt':
    _CF_UNICODETEXT = 13
    _GMEM_MOVEABLE = 0x0002

    def _set_clipboard_text_windows(text):
        """Windows 原生 API 写剪贴板，避免 tkinter 在某些场景下失效"""
        value = text if isinstance(text, str) else str(text or "")
        data = value + "\x00"

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # 显式声明签名，避免 64 位下句柄/指针被截断
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_int
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_int
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = ctypes.c_int
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_int
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p

        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            return False

        h_mem = None
        try:
            if not user32.EmptyClipboard():
                return False

            size_bytes = len(data) * ctypes.sizeof(ctypes.c_wchar)
            h_mem = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size_bytes)
            if not h_mem:
                return False

            p_mem = kernel32.GlobalLock(h_mem)
            if not p_mem:
                kernel32.GlobalFree(h_mem)
                return False

            try:
                buf = ctypes.create_unicode_buffer(data)
                ctypes.memmove(p_mem, buf, size_bytes)
            finally:
                kernel32.GlobalUnlock(h_mem)

            if not user32.SetClipboardData(_CF_UNICODETEXT, h_mem):
                kernel32.GlobalFree(h_mem)
                return False

            # SetClipboardData 成功后，内存所有权转移给系统，不能再释放
            h_mem = None
            return True
        finally:
            if h_mem:
                try:
                    kernel32.GlobalFree(h_mem)
                except Exception:
                    pass
            user32.CloseClipboard()

    def _get_clipboard_text_windows():
        """Windows 原生 API 读剪贴板"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_int
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = ctypes.c_int
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_int

        opened = False
        for _ in range(10):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            return ""

        try:
            h_data = user32.GetClipboardData(_CF_UNICODETEXT)
            if not h_data:
                return ""

            p_data = kernel32.GlobalLock(h_data)
            if not p_data:
                return ""

            try:
                text = ctypes.wstring_at(p_data)
                return text if isinstance(text, str) else ""
            finally:
                kernel32.GlobalUnlock(h_data)
        finally:
            user32.CloseClipboard()


class DesktopBridge:
    """前端 JS 可调用的桌面能力桥接"""

    def get_clipboard_text(self):
        """读取系统剪贴板文本（用于终端粘贴，避免浏览器权限弹窗）"""
        if os.name == 'nt':
            try:
                return _get_clipboard_text_windows()
            except Exception:
                pass
        return _get_clipboard_text_tk()

    def set_clipboard_text(self, text):
        """写入系统剪贴板文本（用于前端复制，避免依赖已弃用 Web API）"""
        value = text if isinstance(text, str) else str(text or "")
        if os.name == 'nt':
            try:
                if _set_clipboard_text_windows(value):
                    # 写后读回校验，避免“假成功”
                    return _get_clipboard_text_windows() == value
            except Exception:
                pass
        if _set_clipboard_text_tk(value):
            return _get_clipboard_text_tk() == value
        return False

def find_free_port():
    """动态寻找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def start_backend(port, logs_dir):
    """在后台线程运行 FastAPI"""
    try:
        # 打包后 backend 内部使用 from xxx import，需将 backend 目录加入 path
        if hasattr(sys, '_MEIPASS'):
            backend_dir = os.path.join(sys._MEIPASS, 'backend')
            if backend_dir not in sys.path:
                sys.path.insert(0, backend_dir)
        from backend.main import app
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
    except Exception as e:
        try:
            err_file = os.path.join(logs_dir, "startup.log")
            with open(err_file, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 后端启动失败: {e}\n")
                import traceback
                f.write(traceback.format_exc())
        except Exception:
            pass

# 默认 .env 内容（当从 bundle 复制失败时的兜底，需与 .env.example 保持一致的加密/鉴权变量）
_DEFAULT_ENV = """# 服务配置
SERVER_HOST=0.0.0.0
SERVER_PORT=9000

# 数据库配置
DB_PATH=../config/xterm.db

# 加密和安全配置（必须与 .env.example 一致，否则数据库解密/登录会异常）
ENCRYPTION_KEY=2QjFKrIn5ET3--xr5uc76YyDD_kUCeh8wU9N2OUnE7Q=
JWT_SECRET=xterm_jwt_secret_change_me_in_prod_12345
APP_PASSWORD=admin

# 默认 AI 配置 (兜底，推荐存入数据库)
AI_API_KEY=sk-xxxxxx
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4o-mini
AI_SYSTEM_PROMPT="你是一个专业的运维 AI 助手。你精通 Linux 系统管理、安全加固和故障排查。请用简洁、准确的语言回答用户，并在必要时提供可执行的 Shell 命令。请注意，你当前的运行环境是一个独立的智能终端。"
"""

def _load_env_to_os(env_path):
    """将 .env 文件中的变量加载到 os.environ，确保打包后鉴权配置生效"""
    if not env_path or not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k:
                        os.environ[k] = v
    except Exception:
        pass

def init_environment(app_root, bundle_path):
    """
    初始化环境：确保 .env 和数据库存在
    app_root: 用户数据持久化目录 (可执行文件所在目录)
    bundle_path: 资源包目录 (PyInstaller 解压后的临时目录)
    """
    # 1. 检查并初始化 .env
    env_path = os.path.join(app_root, ".env")
    if not os.path.exists(env_path):
        _log(f"[*] 正在初始化配置文件: {env_path}")
        env_example = os.path.join(bundle_path, ".env.example")
        try:
            if os.path.exists(env_example):
                shutil.copy2(env_example, env_path)
            else:
                raise FileNotFoundError(".env.example not in bundle")
        except (PermissionError, OSError, FileNotFoundError):
            # 临时目录权限受限（如 F: 盘）时，直接写入默认内容
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(_DEFAULT_ENV)

    # 2. 检查并初始化数据库
    # 注意：这里我们强制将数据库放在 app_root/config 目录下以实现持久化
    db_dir = os.path.join(app_root, "config")
    db_path = os.path.join(db_dir, "xterm.db")
    if not os.path.exists(db_path):
        os.makedirs(db_dir, exist_ok=True)
        db_example = os.path.join(bundle_path, "config", "xterm.db.example")
        try:
            if os.path.exists(db_example):
                _log(f"[*] 正在根据模板初始化数据库: {db_path}")
                shutil.copy2(db_example, db_path)
            else:
                raise FileNotFoundError("xterm.db.example not in bundle")
        except (PermissionError, OSError, FileNotFoundError):
            # 复制失败时创建空文件，后端 Database 会自动初始化表结构
            open(db_path, "a").close()

    # 3. 创建 logs 目录并设置路径（打包后日志输出到 exe 同级 logs 下）
    logs_dir = os.path.join(app_root, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    # 立即写入启动标记，确认目录可写且便于排查
    def _startup_log(msg):
        try:
            with open(os.path.join(logs_dir, "startup.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass
    _startup_log(f"程序启动, app_root={app_root}, bundle_path={bundle_path}")
    _startup_log(f"init: .env={env_path} 存在={os.path.exists(env_path)}")
    _startup_log(f"init: db_path={db_path} 存在={os.path.exists(db_path)}")

    return env_path, db_path, logs_dir

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
    env_path, db_path, logs_dir = init_environment(app_root, bundle_path)
    # 显式加载 .env 到环境变量，确保 APP_PASSWORD/JWT_SECRET 等在后端启动前已就绪
    _load_env_to_os(env_path)

    def _startup_log(msg):
        try:
            with open(os.path.join(logs_dir, "startup.log"), "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    # 自动分配端口并注入环境变量（打包模式下后端需使用这些路径）
    port = find_free_port()
    os.environ["SERVER_PORT"] = str(port)
    os.environ["DB_PATH"] = db_path
    os.environ["BUNDLE_PATH"] = bundle_path
    os.environ["ENV_PATH"] = env_path
    os.environ["LOG_PATH"] = logs_dir
    
    _startup_log(f"env 已加载到 os.environ: JWT_SECRET={'已设置' if os.getenv('JWT_SECRET') else '未设置'}, APP_PASSWORD={'已设置' if os.getenv('APP_PASSWORD') else '未设置'}")
    _log(f"[>] 正在启动后端服务，端口: {port}...")
    _startup_log(f"启动后端线程, port={port}")
    
    # 启动后端线程
    t = threading.Thread(target=start_backend, args=(port, logs_dir), daemon=True)
    t.start()
    
    # 等待服务就绪
    time.sleep(1.5)
    _startup_log("等待 1.5s 后创建 WebView 窗口")
    
    # 创建桌面窗口（图标路径：打包时在 _MEIPASS，开发时为项目根）
    _icon = os.path.join(sys._MEIPASS, 'static', 'terminal.png') if hasattr(sys, '_MEIPASS') else os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'terminal.png')
    _log("[~] 正在创建桌面窗口...")
    window = webview.create_window(
        'XTerm-AI 智能运维终端',
        f'http://127.0.0.1:{port}/frontend/index.html',
        width=1280,
        height=800,
        min_size=(1024, 768),
        # 显式开启文本选择：修复打包后 AI 对话区无法选中复制的问题
        text_select=True,
        js_api=DesktopBridge()
    )
    # 启动 GUI（icon: Linux 窗口图标；Windows/macOS 主要依赖 PyInstaller --icon 内嵌到 exe）
    kw = {'private_mode': False}
    if os.path.exists(_icon):
        kw['icon'] = _icon
    # Windows 打包版优先使用 Edge(WebView2) 后端，避免回退到兼容性较差引擎
    if os.name == 'nt':
        kw['gui'] = 'edgechromium'
    try:
        webview.start(**kw)
    except Exception as e:
        # 兼容兜底：目标机无 WebView2 或参数不兼容时，回退默认后端避免直接崩溃
        _startup_log(f"WebView 启动失败，参数={kw}，错误={e}")
        if kw.get('gui'):
            kw.pop('gui', None)
            _startup_log("回退到默认 WebView 后端重试")
            webview.start(**kw)
        else:
            raise
