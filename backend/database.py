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
            
            # 7. 历史指标表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS server_stats_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id INTEGER,
                    cpu REAL,
                    mem REAL,
                    disk REAL,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers(id)
                )
            ''')
            
            # 8. 设备类型表 (新增：用于绑定角色)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS device_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value TEXT NOT NULL UNIQUE,
                    icon TEXT,
                    role_id INTEGER,
                    FOREIGN KEY (role_id) REFERENCES roles(id)
                )
            ''')
            
            # 9. 技能表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    display_name TEXT,
                    description TEXT,
                    description_zh TEXT,
                    source TEXT DEFAULT 'local',
                    source_url TEXT,
                    content TEXT,
                    trigger_words TEXT,
                    is_enabled INTEGER DEFAULT 1,
                    install_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_fetched_at TIMESTAMP
                )
            ''')
            
            # 9.5 服务器文档表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS server_docs (
                    server_id INTEGER PRIMARY KEY,
                    content TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (server_id) REFERENCES servers(id) ON DELETE CASCADE
                )
            ''')
            
            # 10. 技能-设备类型关联表 (多对多)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS skill_device_types (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id INTEGER NOT NULL,
                    device_type_id INTEGER NOT NULL,
                    FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE,
                    FOREIGN KEY (device_type_id) REFERENCES device_types(id) ON DELETE CASCADE,
                    UNIQUE(skill_id, device_type_id)
                )
            ''')
            
            # 初始化默认设备类型
            cursor.execute('SELECT count(*) FROM device_types')
            if cursor.fetchone()[0] == 0:
                types = [
                    ('Linux', 'linux', 'fab fa-linux', 3),      # 绑定 Linux 运维专家(ID:3)
                    ('Windows', 'windows', 'fab fa-windows', 4),# 绑定 Windows 运维专家(ID:4)
                    ('H3C', 'h3c', 'fas fa-network-wired', 5),  # 绑定 网络设备运维(ID:5)
                    ('华为', 'huawei', 'fas fa-network-wired', 5),
                    ('锐捷', 'ruijie', 'fas fa-network-wired', 5),
                    ('思科', 'cisco', 'fas fa-network-wired', 5),
                    ('其它', 'other', 'fas fa-microchip', None)
                ]
                cursor.executemany('INSERT INTO device_types (name, value, icon, role_id) VALUES (?, ?, ?, ?)', types)

            # 自动迁移：skills 表增加 skill_path（技能商店远程刷新用）
            cursor.execute("PRAGMA table_info(skills)")
            skill_cols = [c[1] for c in cursor.fetchall()]
            if 'skill_path' not in skill_cols:
                cursor.execute("ALTER TABLE skills ADD COLUMN skill_path TEXT DEFAULT '.agent-skills'")

            # 自动迁移：servers 表增加 device_type_id
            cursor.execute("PRAGMA table_info(servers)")
            server_cols = [c[1] for c in cursor.fetchall()]
            if 'device_type_id' not in server_cols:
                cursor.execute("ALTER TABLE servers ADD COLUMN device_type_id INTEGER")
                # 尝试通过已有的 device_type 字符串匹配 ID
                cursor.execute("SELECT id, value FROM device_types")
                types = cursor.fetchall()
                for tid, val in types:
                    cursor.execute("UPDATE servers SET device_type_id = ? WHERE device_type = ?", (tid, val))
            
            # 初始化默认设置
            default_settings = {
                "log_enabled": "1",
                "log_path": "../logs",
                "log_max_size": "10",
                "log_backup_count": "10",
                "log_level": "INFO",
                "skills_enabled": "1"
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
            # 联表查询获取 device_type 的具体 value
            cursor.execute('''
                SELECT s.*, dt.value as device_type_value, dt.name as device_type_name
                FROM servers s
                LEFT JOIN device_types dt ON s.device_type_id = dt.id
                ORDER BY group_name, name
            ''')
            rows = [dict(row) for row in cursor.fetchall()]
            # 解密关键字段
            for r in rows:
                r['password'] = self._decrypt(r.get('password'))
                r['private_key'] = self._decrypt(r.get('private_key'))
                # 兼容旧代码：如果 device_type_value 为空，则用原有的 device_type 字符串
                if not r.get('device_type_value'):
                    r['device_type_value'] = r.get('device_type') or 'linux'
            return rows

    def get_server_by_id(self, server_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, dt.value as device_type_value, dt.name as device_type_name
                FROM servers s
                LEFT JOIN device_types dt ON s.device_type_id = dt.id
                WHERE s.id = ?
            ''', (server_id,))
            row = cursor.fetchone()
            if row:
                res = dict(row)
                res['password'] = self._decrypt(res.get('password'))
                res['private_key'] = self._decrypt(res.get('private_key'))
                if not res.get('device_type_value'):
                    res['device_type_value'] = res.get('device_type') or 'linux'
                return res
            return None

    def add_server(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            encrypted_password = self._encrypt(data.get('password'))
            encrypted_private_key = self._encrypt(data.get('private_key'))
            
            # 从 device_type_id 获取对应的 value 字符串（保持兼容）
            device_type_id = data.get('device_type_id')
            device_type_str = data.get('device_type', 'linux')
            if device_type_id:
                cursor.execute("SELECT value FROM device_types WHERE id = ?", (device_type_id,))
                row = cursor.fetchone()
                if row: device_type_str = row[0]
            
            cursor.execute('''
                INSERT INTO servers (name, host, port, username, password, private_key, group_name, device_type_id, device_type, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (data['name'], data['host'], data.get('port', 22), data['username'], encrypted_password, 
                  encrypted_private_key, data.get('group_name', 'default'), device_type_id, device_type_str, data.get('description')))
            conn.commit()
            return cursor.lastrowid

    def update_server(self, server_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            encrypted_password = self._encrypt(data.get('password'))
            encrypted_private_key = self._encrypt(data.get('private_key'))
            
            # 同步更新旧的 device_type 字符串
            device_type_id = data.get('device_type_id')
            device_type_str = data.get('device_type', 'linux')
            if device_type_id:
                cursor.execute("SELECT value FROM device_types WHERE id = ?", (device_type_id,))
                row = cursor.fetchone()
                if row: device_type_str = row[0]

            set_clauses = ["name=?", "host=?", "port=?", "username=?", "group_name=?", "device_type_id=?", "device_type=?", "description=?"]
            params = [data['name'], data['host'], data['port'], data['username'], data['group_name'], device_type_id, device_type_str, data.get('description')]
            
            if data.get('password'):
                set_clauses.append("password=?")
                params.append(encrypted_password)
            if data.get('private_key'):
                set_clauses.append("private_key=?")
                params.append(encrypted_private_key)
            
            params.append(server_id)
            sql = f"UPDATE servers SET {', '.join(set_clauses)} WHERE id=?"
            cursor.execute(sql, tuple(params))
            conn.commit()

    def delete_server(self, server_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM server_stats_history WHERE server_id = ?', (server_id,))
            cursor.execute('DELETE FROM server_docs WHERE server_id = ?', (server_id,))
            cursor.execute('DELETE FROM servers WHERE id = ?', (server_id,))
            conn.commit()

    # --- 服务器文档 ---
    def get_server_doc(self, server_id):
        """获取服务器文档，不存在返回 None"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT server_id, content, created_at, updated_at FROM server_docs WHERE server_id = ?', (server_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_server_doc(self, server_id, content):
        """创建或更新服务器文档"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO server_docs (server_id, content, created_at, updated_at)
                VALUES (?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(server_id) DO UPDATE SET content = excluded.content, updated_at = datetime('now')
            ''', (server_id, content or ''))
            conn.commit()

    def delete_server_doc(self, server_id):
        """删除服务器文档"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM server_docs WHERE server_id = ?', (server_id,))
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

    def update_device_type_role_bindings(self, role_id, device_type_ids):
        """更新设备类型与角色的绑定关系"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 先将原先绑定该角色的所有类型解绑
            cursor.execute('UPDATE device_types SET role_id = NULL WHERE role_id = ?', (role_id,))
            # 2. 绑定新的类型
            if device_type_ids:
                for dt_id in device_type_ids:
                    cursor.execute('UPDATE device_types SET role_id = ? WHERE id = ?', (role_id, dt_id))
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

    # --- 设备类型相关 ---
    def get_all_device_types(self):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT dt.*, r.name as role_name 
                FROM device_types dt
                LEFT JOIN roles r ON dt.role_id = r.id
                ORDER BY dt.id ASC
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def add_device_type(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO device_types (name, value, icon, role_id)
                VALUES (?, ?, ?, ?)
            ''', (data['name'], data['value'], data.get('icon', 'fas fa-microchip'), data.get('role_id')))
            conn.commit()
            return cursor.lastrowid

    def update_device_type(self, type_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE device_types 
                SET name=?, value=?, icon=?, role_id=?
                WHERE id=?
            ''', (data['name'], data['value'], data['icon'], data.get('role_id'), type_id))
            conn.commit()

    def delete_device_type(self, type_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. 先找到该类型的 value
            cursor.execute('SELECT value FROM device_types WHERE id = ?', (type_id,))
            row = cursor.fetchone()
            if row:
                # 2. 将引用该类型的服务器重置为 null 或默认值 (这里假设 ID 为 1 是默认)
                cursor.execute('UPDATE servers SET device_type_id = NULL WHERE device_type_id = ?', (type_id,))
                # 3. 删除类型
                cursor.execute('DELETE FROM device_types WHERE id = ?', (type_id,))
            conn.commit()

    # --- 监控历史记录 ---
    def add_stats_history(self, server_id, cpu, mem, disk=0):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO server_stats_history (server_id, cpu, mem, disk)
                VALUES (?, ?, ?, ?)
            ''', (server_id, cpu, mem, disk))
            conn.commit()

    def get_stats_history(self, server_id, minutes=30):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 这里的 timestamp 是 UTC 时间，sqlite 的 datetime('now') 也是 UTC
            cursor.execute('''
                SELECT cpu, mem, disk, strftime('%H:%M', datetime(timestamp, 'localtime')) as time_label
                FROM server_stats_history 
                WHERE server_id = ? 
                AND timestamp >= datetime('now', ?)
                ORDER BY timestamp ASC
            ''', (server_id, f'-{minutes} minutes'))
            return [dict(row) for row in cursor.fetchall()]

    def clean_stats_history(self, days=7):
        """清理 7 天之前的历史记录"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM server_stats_history WHERE timestamp < datetime('now', ?)", (f'-{days} days',))
            conn.commit()

    def clear_stats_for_server(self, server_id):
        """清除指定服务器的所有状态历史记录"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM server_stats_history WHERE server_id = ?", (server_id,))
            conn.commit()

    def clear_all_stats_history(self):
        """清除 server_stats_history 表中所有记录"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM server_stats_history")
            conn.commit()

    # --- 技能相关操作 ---
    def get_all_skills(self, enabled_only=False, device_type_id=None):
        """获取所有技能，支持按启用状态、设备类型过滤"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if device_type_id is not None:
                cursor.execute('''
                    SELECT DISTINCT s.* FROM skills s
                    INNER JOIN skill_device_types sdt ON s.id = sdt.skill_id
                    WHERE sdt.device_type_id = ?
                    {enabled_filter}
                    ORDER BY s.created_at DESC
                '''.format(enabled_filter='AND s.is_enabled = 1' if enabled_only else ''), (device_type_id,))
            else:
                if enabled_only:
                    cursor.execute('SELECT * FROM skills WHERE is_enabled = 1 ORDER BY created_at DESC')
                else:
                    cursor.execute('SELECT * FROM skills ORDER BY created_at DESC')
            rows = cursor.fetchall()
            result = [dict(row) for row in rows]
            for r in result:
                r['bound_device_type_ids'] = self._get_skill_device_type_ids(r['id'])
            return result

    def _get_skill_device_type_ids(self, skill_id):
        """获取技能绑定的设备类型 ID 列表"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT device_type_id FROM skill_device_types WHERE skill_id = ?', (skill_id,))
            return [row[0] for row in cursor.fetchall()]

    def get_skill_by_id(self, skill_id):
        """获取单个技能详情"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM skills WHERE id = ?', (skill_id,))
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result['bound_device_type_ids'] = self._get_skill_device_type_ids(skill_id)
            return result

    def get_skill_by_name(self, name):
        """按 name 查找技能"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM skills WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_skill(self, data):
        """创建技能"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            trigger_words = json.dumps(data.get('trigger_words') or []) if isinstance(data.get('trigger_words'), list) else data.get('trigger_words')
            cursor.execute('''
                INSERT INTO skills (name, display_name, description, description_zh, source, source_url, skill_path, content, trigger_words, is_enabled, install_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['name'],
                data.get('display_name') or data['name'],
                data.get('description') or '',
                data.get('description_zh'),
                data.get('source', 'local'),
                data.get('source_url'),
                data.get('skill_path') or '.agent-skills',
                data.get('content') or '',
                trigger_words,
                data.get('is_enabled', 1),
                data.get('install_count', 0)
            ))
            skill_id = cursor.lastrowid
            conn.commit()
            return skill_id

    def update_skill(self, skill_id, data):
        """更新技能"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            trigger_words = data.get('trigger_words')
            if isinstance(trigger_words, list):
                trigger_words = json.dumps(trigger_words)
            cursor.execute('''
                UPDATE skills SET
                    name=?, display_name=?, description=?, description_zh=?, source=?, source_url=?,
                    skill_path=?, content=?, trigger_words=?, is_enabled=?, install_count=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?
            ''', (
                data.get('name'),
                data.get('display_name'),
                data.get('description'),
                data.get('description_zh'),
                data.get('source'),
                data.get('source_url'),
                data.get('skill_path') or '.agent-skills',
                data.get('content'),
                trigger_words,
                data.get('is_enabled', 1),
                data.get('install_count', 0),
                skill_id
            ))
            conn.commit()

    def delete_skill(self, skill_id):
        """删除技能（级联删除 skill_device_types）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM skill_device_types WHERE skill_id = ?', (skill_id,))
            cursor.execute('DELETE FROM skills WHERE id = ?', (skill_id,))
            conn.commit()

    def toggle_skill(self, skill_id):
        """切换技能启用/禁用状态"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE skills SET is_enabled = 1 - is_enabled, updated_at=CURRENT_TIMESTAMP WHERE id = ?', (skill_id,))
            conn.commit()
            cursor.execute('SELECT is_enabled FROM skills WHERE id = ?', (skill_id,))
            row = cursor.fetchone()
            return row[0] if row else None

    def update_skill_device_type_bindings(self, skill_id, device_type_ids):
        """更新技能与设备类型的绑定关系"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM skill_device_types WHERE skill_id = ?', (skill_id,))
            if device_type_ids:
                for dt_id in device_type_ids:
                    cursor.execute('INSERT OR IGNORE INTO skill_device_types (skill_id, device_type_id) VALUES (?, ?)', (skill_id, dt_id))
            conn.commit()

    def get_skills_for_device_type(self, device_type_value, enabled_only=True):
        """根据设备类型 value 获取应注入的技能列表（供 AI 使用）"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.id, s.name, s.display_name, s.content
                FROM skills s
                INNER JOIN skill_device_types sdt ON s.id = sdt.skill_id
                INNER JOIN device_types dt ON sdt.device_type_id = dt.id
                WHERE dt.value = ? AND (? = 0 OR s.is_enabled = 1)
                ORDER BY s.created_at ASC
            ''', (device_type_value, 0 if not enabled_only else 1))
            return [dict(row) for row in cursor.fetchall()]
