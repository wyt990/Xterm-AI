import paramiko
import threading
import time
import socket
import traceback

def _log(module, msg):
    try:
        from logger import app_logger
        app_logger.info(module, msg)
    except Exception:
        print(f"[{module}] {msg}")

class SSHHandler:
    def __init__(self, host, port, username, password=None, private_key=None, proxy=None):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.private_key = private_key
        self.proxy = proxy  # 代理配置 dict，用于 SSH 连接经代理
        self.client = None
        self.channel = None
        self.transport = None

    def connect(self):
        _log("SSH", self._build_connect_log())
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            sock = self._resolve_connect_sock()
            if self.proxy and not sock:
                _log("SSH", "代理 socket 创建失败")
                return False

            _conn_kw = {"timeout": 10, "allow_agent": False, "look_for_keys": False}
            if sock:
                _conn_kw["sock"] = sock
                _log("SSH", "经代理连接...")
            else:
                _log("SSH", "直连...")

            if self.private_key:
                key = paramiko.RSAKey.from_private_key_file(self.private_key)
                self.client.connect(self.host, self.port, self.username, pkey=key, **_conn_kw)
            else:
                try:
                    self.client.connect(self.host, self.port, self.username, self.password, **_conn_kw)
                except paramiko.ssh_exception.AuthenticationException:
                    _log("SSH", "password 认证失败，尝试 keyboard-interactive（H3C/华为等设备）")
                    if not self._connect_keyboard_interactive():
                        raise

            _log("SSH", "SSH 握手成功，创建 shell channel...")
            self.channel = self.client.invoke_shell(term='xterm', width=120, height=40)
            self.channel.setblocking(0)
            _log("SSH", f"连接成功: {self.host}:{self.port}")
            return True
        except Exception as e:
            tb = traceback.format_exc()
            _log("SSH", f"连接失败: {self.host}:{self.port} error={type(e).__name__}: {e}")
            _log("SSH", f"异常堆栈:\n{tb}")
            return False

    def _build_connect_log(self) -> str:
        auth_type = "private_key" if self.private_key else "password"
        return f"连接开始: {self.host}:{self.port} user={self.username} auth={auth_type} proxy={bool(self.proxy)}"

    def _resolve_connect_sock(self):
        if not self.proxy:
            return None
        from proxy_utils import should_skip_proxy
        skip = should_skip_proxy(self.proxy, self.host)
        _log("SSH", f"代理检查: skip={skip}")
        if skip:
            return None
        _log("SSH", f"创建代理 socket: {self.proxy.get('type')} {self.proxy.get('host')}:{self.proxy.get('port')}")
        return self._create_proxy_socket()

    def _connect_keyboard_interactive(self):
        """H3C/华为等设备可能要求 keyboard-interactive，password 失败时尝试"""
        try:
            if self.proxy:
                sock = self._create_proxy_socket()
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((self.host, self.port))
            if sock is None:
                return False
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            trans = paramiko.Transport(sock)
            trans.start_client()
            key = trans.get_remote_server_key()
            self.client._host_keys.add(self.host, key.get_name(), key)
            def _handler(title, instructions, prompt_list):
                return [self.password] if self.password else []
            trans.auth_interactive(self.username, _handler)
            self.client._transport = trans
            return True
        except Exception as e:
            _log("SSH", f"keyboard-interactive 失败: {type(e).__name__}: {e}")
            self.client = None
            return False

    def _create_proxy_socket(self):
        """通过 PySocks 创建经代理连接的 socket"""
        try:
            import socks
            proxy_type = socks.SOCKS5 if (self.proxy.get("type") or "").lower() == "socks5" else socks.HTTP
            sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_proxy(
                proxy_type,
                self.proxy.get("host", ""),
                int(self.proxy.get("port") or 0),
                username=self.proxy.get("username") or None,
                password=self.proxy.get("password") or None,
            )
            sock.connect((self.host, self.port))
            _log("SSH", "代理 socket 连接成功")
            return sock
        except Exception as e:
            _log("SSH", f"代理 socket 错误: {type(e).__name__}: {e}")
            return None

    def resize_pty(self, width, height):
        if self.channel:
            self.channel.resize_pty(width=width, height=height)

    def write(self, data):
        if self.channel:
            self.channel.send(data)

    def read(self):
        if self.channel and self.channel.recv_ready():
            return self.channel.recv(8192).decode('utf-8', errors='ignore')
        return None

    def open_sftp(self):
        if not self.client:
            _log("SSH", "open_sftp: 需先 connect")
            if not self.connect():
                return None
        try:
            sftp = self.client.open_sftp()
            _log("SSH", f"SFTP 已打开: {self.host}:{self.port}")
            return sftp
        except Exception as e:
            _log("SSH", f"SFTP 打开失败: {type(e).__name__}: {e}")
            return None

    def close(self):
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()
