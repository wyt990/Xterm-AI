import paramiko
import threading
import time
import socket

class SSHHandler:
    def __init__(self, host, port, username, password=None, private_key=None):
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.private_key = private_key
        self.client = None
        self.channel = None
        self.transport = None

    def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if self.private_key:
                # 简单实现，实际生产应处理私钥文件加载
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
