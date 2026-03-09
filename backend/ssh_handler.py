import paramiko
import threading
import time
import socket

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
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            sock = None
            if self.proxy:
                from proxy_utils import should_skip_proxy
                if not should_skip_proxy(self.proxy, self.host):
                    sock = self._create_proxy_socket()

            if sock:
                if self.private_key:
                    key = paramiko.RSAKey.from_private_key_file(self.private_key)
                    self.client.connect(self.host, self.port, self.username, pkey=key, timeout=10, sock=sock)
                else:
                    self.client.connect(self.host, self.port, self.username, self.password, timeout=10, sock=sock)
            else:
                if self.private_key:
                    key = paramiko.RSAKey.from_private_key_file(self.private_key)
                    self.client.connect(self.host, self.port, self.username, pkey=key, timeout=10)
                else:
                    self.client.connect(self.host, self.port, self.username, self.password, timeout=10)
            
            self.channel = self.client.invoke_shell(term='xterm', width=120, height=40)
            self.channel.setblocking(0) # 设置非阻塞模式
            return True
        except Exception as e:
            print(f"SSH Connection Error: {e}")
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
            return sock
        except Exception as e:
            print(f"Proxy socket error: {e}")
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
            if not self.connect():
                return None
        try:
            return self.client.open_sftp()
        except Exception as e:
            print(f"SFTP Open Error: {e}")
            return None

    def close(self):
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()
