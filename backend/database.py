import sqlite3
import os
import json
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        # 初始化加密器
        encryption_key = os.getenv("ENCRYPTION_KEY")
        if not encryption_key:
            # 这里的 fallback 仅用于防止程序崩溃，实际生产应确保配置了秘钥
            print("警告: 未配置 ENCRYPTION_KEY，将使用临时秘钥。")
            self.fernet = Fernet(Fernet.generate_key())
        else:
            self.fernet = Fernet(encryption_key.encode())
            
        self._init_db()

    def _encrypt(self, text):
        if not text: return None
        return self.fernet.encrypt(text.encode()).decode()

    def _decrypt(self, encrypted_text):
        if not encrypted_text: return None
        try:
            return self.fernet.decrypt(encrypted_text.encode()).decode()
        except Exception:
            # 如果解密失败（可能是旧的明文数据），原样返回，由迁移脚本处理
            return encrypted_text

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 服务器表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER DEFAULT 22,
                    username TEXT DEFAULT 'root',
                    password TEXT,
                    private_key TEXT,
                    group_name TEXT DEFAULT 'default',
                    device_type TEXT DEFAULT 'linux',
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 2. AI 配置表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_endpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    capabilities TEXT DEFAULT '["text"]',
                    is_active INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 自动迁移：检查缺失的 capabilities 列
            cursor.execute("PRAGMA table_info(ai_endpoints)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'capabilities' not in columns:
                cursor.execute("ALTER TABLE ai_endpoints ADD COLUMN capabilities TEXT DEFAULT '[\"text\"]'")
            
            # 3. 角色表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    ai_endpoint_id INTEGER,
                    is_active INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ai_endpoint_id) REFERENCES ai_endpoints(id)
                )
            ''')
            
            # 4. 系统设置表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            
            # 5. 命令分组表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS command_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 6. 快捷命令表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id INTEGER,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    auto_cr INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (group_id) REFERENCES command_groups(id)
                )
            ''')
            
            # 初始化默认设置
            default_settings = {
                "log_enabled": "1",
                "log_path": "../logs",
                "log_max_size": "10",
                "log_backup_count": "10",
                "log_level": "INFO"
            }
            for k, v in default_settings.items():
                cursor.execute('INSERT OR IGNORE INTO system_settings (key, value) VALUES (?, ?)', (k, v))
            
            # 插入默认命令分组和示例命令
            cursor.execute('SELECT count(*) FROM command_groups')
            if cursor.fetchone()[0] == 0:
                cursor.execute('INSERT INTO command_groups (name) VALUES (?)', ('常用命令',))
                group_id = cursor.lastrowid
                example_commands = [
                    ('查看系统负载', 'uptime', 1),
                    ('查看内存占用', 'free -h', 1),
                    ('查看磁盘空间', 'df -h', 1),
                    ('查看网络连接', 'netstat -ntlp', 1)
                ]
                for name, content, auto_cr in example_commands:
                    cursor.execute('INSERT INTO commands (group_id, name, content, auto_cr) VALUES (?, ?, ?, ?)',
                                 (group_id, name, content, auto_cr))
            
            # 插入默认角色 (已精简，命令协议已硬编码到后端)
            cursor.execute('SELECT count(*) FROM roles')
            if cursor.fetchone()[0] == 0:
                default_prompt = """你是一个专业的运维 AI 专家，精通 Linux 系统管理、架构设计、安全加固和复杂故障排查。

你的职责与准则：
1. 身份定位：你拥有深厚的系统底层、网络协议及自动化运维经验。
2. 回复风格：请始终保持专业、客观、简洁。直接针对用户问题提供核心诊断思路。
3. 辅助决策：在分析问题时，请结合当前系统环境给出准确的操作建议或排查步骤。
4. 运行环境：你当前运行在一个独立的智能终端中，能够直接辅助用户进行实时的系统运维操作。"""
                cursor.execute('INSERT INTO roles (name, system_prompt, is_active) VALUES (?, ?, ?)', 
                             ('智能运维专家', default_prompt, 1))
            
            conn.commit()

    # --- 服务器相关操作 ---
    def get_all_servers(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM servers ORDER BY group_name, name')
            rows = [dict(row) for row in cursor.fetchall()]
            # 解密关键字段
            for r in rows:
                r['password'] = self._decrypt(r.get('password'))
                r['private_key'] = self._decrypt(r.get('private_key'))
            return rows

    def get_server_by_id(self, server_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
            row = cursor.fetchone()
            if row:
                res = dict(row)
                res['password'] = self._decrypt(res.get('password'))
                res['private_key'] = self._decrypt(res.get('private_key'))
                return res
            return None

    def add_server(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            encrypted_password = self._encrypt(data.get('password'))
            encrypted_private_key = self._encrypt(data.get('private_key'))
            cursor.execute('''
                INSERT INTO servers (name, host, port, username, password, private_key, group_name, device_type, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data['name'], data['host'], data.get('port', 22), data['username'], encrypted_password, 
                  encrypted_private_key, data.get('group_name', 'default'), data.get('device_type', 'linux'), data.get('description')))
            conn.commit()
            return cursor.lastrowid

    def update_server(self, server_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            encrypted_password = self._encrypt(data.get('password'))
            encrypted_private_key = self._encrypt(data.get('private_key'))
            cursor.execute('''
                UPDATE servers 
                SET name=?, host=?, port=?, username=?, password=?, private_key=?, group_name=?, device_type=?, description=?
                WHERE id=?
            ''', (data['name'], data['host'], data['port'], data['username'], encrypted_password, 
                  encrypted_private_key, data['group_name'], data['device_type'], data.get('description'), server_id))
            conn.commit()

    def delete_server(self, server_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM servers WHERE id = ?', (server_id,))
            conn.commit()

    # --- AI 配置相关操作 ---
    def get_active_ai_endpoint(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ai_endpoints WHERE is_active = 1 LIMIT 1')
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_ai_endpoints(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ai_endpoints ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_ai_endpoint_by_id(self, ai_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ai_endpoints WHERE id = ?', (ai_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_ai_endpoint(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            capabilities = json.dumps(data.get('capabilities', ['text']))
            cursor.execute('''
                INSERT INTO ai_endpoints (name, api_key, base_url, model, capabilities, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (data['name'], data['api_key'], data['base_url'], data['model'], capabilities, data.get('is_active', 0)))
            conn.commit()
            return cursor.lastrowid

    def update_ai_endpoint(self, ai_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            capabilities = json.dumps(data.get('capabilities', ['text']))
            cursor.execute('''
                UPDATE ai_endpoints 
                SET name=?, api_key=?, base_url=?, model=?, capabilities=?
                WHERE id=?
            ''', (data['name'], data['api_key'], data['base_url'], data['model'], capabilities, ai_id))
            conn.commit()

    def set_active_ai(self, ai_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE ai_endpoints SET is_active = 0')
            cursor.execute('UPDATE ai_endpoints SET is_active = 1 WHERE id = ?', (ai_id,))
            conn.commit()

    def delete_ai_endpoint(self, ai_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM ai_endpoints WHERE id = ?', (ai_id,))
            conn.commit()

    # --- 角色相关操作 ---
    def get_all_roles(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT r.*, a.name as ai_name 
                FROM roles r 
                LEFT JOIN ai_endpoints a ON r.ai_endpoint_id = a.id 
                ORDER BY r.is_active DESC, r.created_at DESC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_active_role(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM roles WHERE is_active = 1 LIMIT 1')
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_role_by_id(self, role_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM roles WHERE id = ?', (role_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_role(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO roles (name, system_prompt, ai_endpoint_id, is_active)
                VALUES (?, ?, ?, ?)
            ''', (data['name'], data['system_prompt'], data.get('ai_endpoint_id'), data.get('is_active', 0)))
            conn.commit()
            return cursor.lastrowid

    def update_role(self, role_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE roles 
                SET name=?, system_prompt=?, ai_endpoint_id=?
                WHERE id=?
            ''', (data['name'], data['system_prompt'], data.get('ai_endpoint_id'), role_id))
            conn.commit()

    def delete_role(self, role_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM roles WHERE id = ?', (role_id,))
            conn.commit()

    def set_active_role(self, role_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE roles SET is_active = 0')
            cursor.execute('UPDATE roles SET is_active = 1 WHERE id = ?', (role_id,))
            conn.commit()

    # --- 系统设置操作 ---
    def get_system_settings(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT key, value FROM system_settings')
            return {row[0]: row[1] for row in cursor.fetchall()}

    def update_system_setting(self, key, value):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE system_settings SET value = ? WHERE key = ?', (value, key))
            conn.commit()

    # --- 命令分组相关操作 ---
    def get_all_command_groups(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM command_groups ORDER BY created_at ASC')
            return [dict(row) for row in cursor.fetchall()]

    def add_command_group(self, name):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO command_groups (name) VALUES (?)', (name,))
            conn.commit()
            return cursor.lastrowid

    def update_command_group(self, group_id, name):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE command_groups SET name = ? WHERE id = ?', (name, group_id))
            conn.commit()

    def delete_command_group(self, group_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 级联删除该分组下的所有命令
            cursor.execute('DELETE FROM commands WHERE group_id = ?', (group_id,))
            cursor.execute('DELETE FROM command_groups WHERE id = ?', (group_id,))
            conn.commit()

    # --- 快捷命令相关操作 ---
    def get_commands_by_group(self, group_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM commands WHERE group_id = ? ORDER BY created_at ASC', (group_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_command(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO commands (group_id, name, content, auto_cr) 
                VALUES (?, ?, ?, ?)
            ''', (data['group_id'], data['name'], data['content'], data.get('auto_cr', 1)))
            conn.commit()
            return cursor.lastrowid

    def update_command(self, cmd_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE commands 
                SET group_id=?, name=?, content=?, auto_cr=?
                WHERE id=?
            ''', (data['group_id'], data['name'], data['content'], data.get('auto_cr', 1), cmd_id))
            conn.commit()

    def delete_command(self, cmd_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM commands WHERE id = ?', (cmd_id,))
            conn.commit()
