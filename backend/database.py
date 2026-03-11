import sqlite3
import os
import json
from typing import Optional
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()
NETWORK_ICON = 'fas fa-network-wired'

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
        except Exception as e:
            # 解密失败：可能是明文、错误密钥、或损坏数据。原样返回以便调用方使用。
            try:
                from logger import app_logger
                app_logger.info("数据库", f"解密失败(将使用原值): {type(e).__name__}")
            except Exception:
                pass
            return encrypted_text

    def _json_list_to_text(self, value, default=None):
        base = default if default is not None else []
        if isinstance(value, list):
            arr = value
        elif isinstance(value, str):
            txt = value.strip()
            if not txt:
                arr = base
            else:
                try:
                    parsed = json.loads(txt)
                    arr = parsed if isinstance(parsed, list) else base
                except Exception:
                    arr = [x.strip() for x in txt.split(",") if x.strip()]
        else:
            arr = base
        cleaned = []
        for x in arr:
            s = str(x).strip()
            if s and s not in cleaned:
                cleaned.append(s)
        return json.dumps(cleaned, ensure_ascii=False)

    def _json_text_to_list(self, value, default=None):
        base = default if default is not None else []
        if isinstance(value, list):
            arr = value
        elif isinstance(value, str):
            txt = value.strip()
            if not txt:
                arr = base
            else:
                try:
                    parsed = json.loads(txt)
                    arr = parsed if isinstance(parsed, list) else base
                except Exception:
                    arr = [x.strip() for x in txt.split(",") if x.strip()]
        else:
            arr = base
        cleaned = []
        for x in arr:
            s = str(x).strip()
            if s and s not in cleaned:
                cleaned.append(s)
        return cleaned

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):  # NOSONAR - 初始化流程需保持向后兼容与幂等迁移顺序
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
                    role_scope TEXT NOT NULL DEFAULT 'ops',
                    is_active INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ai_endpoint_id) REFERENCES ai_endpoints(id)
                )
            ''')
            cursor.execute("PRAGMA table_info(roles)")
            role_cols = [c[1] for c in cursor.fetchall()]
            if 'role_scope' not in role_cols:
                cursor.execute("ALTER TABLE roles ADD COLUMN role_scope TEXT NOT NULL DEFAULT 'ops'")
            
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
                    ('H3C', 'h3c', NETWORK_ICON, 5),  # 绑定 网络设备运维(ID:5)
                    ('华为', 'huawei', NETWORK_ICON, 5),
                    ('锐捷', 'ruijie', NETWORK_ICON, 5),
                    ('思科', 'cisco', NETWORK_ICON, 5),
                    ('其它', 'other', 'fas fa-microchip', None)
                ]
                cursor.executemany('INSERT INTO device_types (name, value, icon, role_id) VALUES (?, ?, ?, ?)', types)

            # 自动迁移：skills 表增加 skill_path（技能商店远程刷新用）
            cursor.execute("PRAGMA table_info(skills)")
            skill_cols = [c[1] for c in cursor.fetchall()]
            if 'skill_path' not in skill_cols:
                cursor.execute("ALTER TABLE skills ADD COLUMN skill_path TEXT DEFAULT '.agent-skills'")
            if 'scope_tags' not in skill_cols:
                cursor.execute("ALTER TABLE skills ADD COLUMN scope_tags TEXT DEFAULT '[\"ops\"]'")
            cursor.execute(
                "UPDATE skills SET scope_tags='[\"ops\"]' WHERE scope_tags IS NULL OR TRIM(scope_tags)=''"
            )

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
            # 自动迁移：servers 表增加最近连接时间
            if 'last_connected_at' not in server_cols:
                cursor.execute("ALTER TABLE servers ADD COLUMN last_connected_at TIMESTAMP")
            
            # 11. 代理表 (proxy 功能 Phase 1)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS proxies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    username TEXT,
                    password TEXT,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_proxies_name ON proxies(name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_proxies_type ON proxies(type)')
            # 迁移：proxies 表增加 ignore_local 列（忽略本地/局域网连接）
            cursor.execute("PRAGMA table_info(proxies)")
            proxy_cols = [c[1] for c in cursor.fetchall()]
            if 'ignore_local' not in proxy_cols:
                cursor.execute("ALTER TABLE proxies ADD COLUMN ignore_local INTEGER DEFAULT 0")

            # 12. 自进化任务主表（Phase A）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS evolution_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'user',
                    task_type TEXT NOT NULL DEFAULT 'fix',
                    scope TEXT NOT NULL DEFAULT 'backend',
                    risk_level TEXT NOT NULL DEFAULT 'low',
                    status TEXT NOT NULL DEFAULT 'new',
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    approval_status TEXT NOT NULL DEFAULT 'none',
                    max_retries INTEGER NOT NULL DEFAULT 100,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_signature TEXT,
                    error_repeat_count INTEGER NOT NULL DEFAULT 0,
                    acceptance_criteria TEXT DEFAULT '',
                    rollback_plan TEXT DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    final_report TEXT,
                    needs_human_action INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT DEFAULT 'system',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_evolution_tasks_status ON evolution_tasks(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_evolution_tasks_risk ON evolution_tasks(risk_level)')
            cursor.execute("PRAGMA table_info(evolution_tasks)")
            evo_cols = [c[1] for c in cursor.fetchall()]
            if 'approved_by' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN approved_by TEXT")
            if 'approved_at' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN approved_at TIMESTAMP")
            if 'rejected_by' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN rejected_by TEXT")
            if 'rejected_at' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN rejected_at TIMESTAMP")
            if 'rejection_reason' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN rejection_reason TEXT")
            if 'last_run_at' not in evo_cols:
                cursor.execute("ALTER TABLE evolution_tasks ADD COLUMN last_run_at TIMESTAMP")

            # 13. 自进化任务运行记录表（Phase A）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS evolution_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    trigger_type TEXT NOT NULL DEFAULT 'manual',
                    run_status TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    operator TEXT DEFAULT 'system',
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    finished_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES evolution_tasks(id)
                )
            ''')

            # 14. 插件注册表（Phase C）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS plugin_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'installed',
                    runtime TEXT NOT NULL DEFAULT 'python',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    install_path TEXT,
                    installed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_plugin_registry_status ON plugin_registry(status)')

            # 15. 定时任务规则表（Phase C）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'plugin_manifest',
                    task_template_key TEXT DEFAULT '',
                    plugin_id TEXT DEFAULT NULL,
                    cron_expr TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                    window_start TEXT DEFAULT NULL,
                    window_end TEXT DEFAULT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    max_concurrent INTEGER NOT NULL DEFAULT 1,
                    max_retries INTEGER NOT NULL DEFAULT 100,
                    policy_overrides_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_jobs_plugin ON scheduled_jobs(plugin_id)')

            # 16. 经验库（Phase D）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS evolution_experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    error_category TEXT NOT NULL DEFAULT 'unknown',
                    error_signature TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    action_suggestion TEXT NOT NULL DEFAULT '',
                    is_resolved INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(task_id) REFERENCES evolution_tasks(id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_evolution_experiences_signature ON evolution_experiences(error_signature)')

            # 17. 无法修复报告（Phase D）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS evolution_failure_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    report_title TEXT NOT NULL,
                    report_markdown TEXT NOT NULL,
                    notify_channel TEXT NOT NULL DEFAULT 'ui',
                    notify_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(task_id) REFERENCES evolution_tasks(id)
                )
            ''')

            # 18. 数据库迁移审计（Phase D）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    migration_name TEXT NOT NULL,
                    checksum TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'applied',
                    applied_by TEXT NOT NULL DEFAULT 'system',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    rolled_back_at TIMESTAMP
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_schema_migrations_name ON schema_migrations(migration_name)')

            # 初始化默认设置
            default_settings = {
                "log_enabled": "1",
                "log_path": "../logs",
                "log_max_size": "10",
                "log_backup_count": "10",
                "log_level": "INFO",
                "skills_enabled": "1",
                "proxy_for_terminal": "",
                "proxy_for_ai": "",
                "proxy_for_skills": "",
                "skills_scope_filter_mode": "soft",
                "evolution_scheduler_enabled": "1",
                "evolution_scheduler_interval_sec": "30",
                "evolution_scheduler_max_tasks_per_tick": "3",
                "evolution_retry_delay_sec": "60",
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
                cursor.execute('INSERT INTO roles (name, system_prompt, role_scope, is_active) VALUES (?, ?, ?, ?)', 
                             ('智能运维专家', default_prompt, 'ops', 1))
            
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

    def mark_server_connected(self, server_id):
        """记录服务器最近连接时间（用于最近连接列表）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE servers SET last_connected_at = CURRENT_TIMESTAMP WHERE id = ?",
                (server_id,)
            )
            conn.commit()

    def get_recent_servers(self, limit=20):
        """获取最近连接的服务器（按连接时间倒序）"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, dt.value as device_type_value, dt.name as device_type_name
                FROM servers s
                LEFT JOIN device_types dt ON s.device_type_id = dt.id
                WHERE s.last_connected_at IS NOT NULL
                ORDER BY datetime(s.last_connected_at) DESC
                LIMIT ?
            ''', (int(limit),))
            rows = [dict(row) for row in cursor.fetchall()]
            for r in rows:
                # 最近连接列表不返回敏感凭据字段
                r.pop('password', None)
                r.pop('private_key', None)
                if not r.get('device_type_value'):
                    r['device_type_value'] = r.get('device_type') or 'linux'
            return rows

    def clear_recent_connections(self):
        """清空最近连接（仅清空连接时间字段）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE servers SET last_connected_at = NULL WHERE last_connected_at IS NOT NULL")
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
        """更新 AI 端点。api_key 为空或 ******** 时保留原值，避免编辑时误覆盖。"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            capabilities = json.dumps(data.get('capabilities', ['text']))
            api_key = data.get('api_key') or ''
            if not api_key or api_key.strip() == '' or api_key == '********':
                # 保留原有 api_key
                cursor.execute('''
                    UPDATE ai_endpoints 
                    SET name=?, base_url=?, model=?, capabilities=?
                    WHERE id=?
                ''', (data['name'], data['base_url'], data['model'], capabilities, ai_id))
            else:
                cursor.execute('''
                    UPDATE ai_endpoints 
                    SET name=?, api_key=?, base_url=?, model=?, capabilities=?
                    WHERE id=?
                ''', (data['name'], api_key, data['base_url'], data['model'], capabilities, ai_id))
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
    def get_all_roles(self, scope: Optional[str] = "ops"):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if scope:
                cursor.execute('''
                    SELECT r.*, a.name as ai_name
                    FROM roles r
                    LEFT JOIN ai_endpoints a ON r.ai_endpoint_id = a.id
                    WHERE r.role_scope = ?
                    ORDER BY r.is_active DESC, r.created_at DESC
                ''', (scope,))
            else:
                cursor.execute('''
                    SELECT r.*, a.name as ai_name
                    FROM roles r
                    LEFT JOIN ai_endpoints a ON r.ai_endpoint_id = a.id
                    ORDER BY r.is_active DESC, r.created_at DESC
                ''')
            return [dict(row) for row in cursor.fetchall()]

    def get_active_role(self, scope: str = "ops"):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM roles WHERE is_active = 1 AND role_scope = ? LIMIT 1', (scope,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_role_by_id(self, role_id, scope: Optional[str] = None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if scope:
                cursor.execute('SELECT * FROM roles WHERE id = ? AND role_scope = ?', (role_id, scope))
            else:
                cursor.execute('SELECT * FROM roles WHERE id = ?', (role_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_role(self, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO roles (name, system_prompt, ai_endpoint_id, role_scope, is_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                data['name'],
                data['system_prompt'],
                data.get('ai_endpoint_id'),
                data.get('role_scope', 'ops'),
                data.get('is_active', 0),
            ))
            conn.commit()
            return cursor.lastrowid

    def update_role(self, role_id, data):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE roles 
                SET name=?, system_prompt=?, ai_endpoint_id=?, role_scope=?
                WHERE id=?
            ''', (
                data['name'],
                data['system_prompt'],
                data.get('ai_endpoint_id'),
                data.get('role_scope', 'ops'),
                role_id,
            ))
            conn.commit()

    def delete_role(self, role_id):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM roles WHERE id = ?', (role_id,))
            conn.commit()

    def set_active_role(self, role_id, scope: str = "ops"):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE roles SET is_active = 0 WHERE role_scope = ?', (scope,))
            cursor.execute('UPDATE roles SET is_active = 1 WHERE id = ? AND role_scope = ?', (role_id, scope))
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

    def upsert_system_setting(self, key, value):
        """插入或更新系统设置（用于 proxy_bindings 等可能不存在的 key）"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO system_settings (key, value) VALUES (?, ?)',
                (key, str(value) if value is not None else '')
            )
            conn.commit()

    # --- 代理相关操作 ---
    def get_all_proxies(self):
        """获取所有代理，密码解密后返回（API 需脱敏展示）"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM proxies ORDER BY created_at DESC')
            rows = [dict(row) for row in cursor.fetchall()]
            for r in rows:
                r['password'] = self._decrypt(r.get('password'))
            return rows

    def get_proxy_by_id(self, proxy_id):
        """获取单个代理，密码解密"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM proxies WHERE id = ?', (proxy_id,))
            row = cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res['password'] = self._decrypt(res.get('password'))
            return res

    def add_proxy(self, data):
        """新增代理，密码加密存储"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            encrypted_password = self._encrypt(data.get('password'))
            ignore_local = 1 if data.get('ignore_local') else 0
            cursor.execute('''
                INSERT INTO proxies (name, type, host, port, username, password, description, ignore_local)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['name'],
                data['type'],
                data['host'],
                data['port'],
                data.get('username'),
                encrypted_password,
                data.get('description'),
                ignore_local,
            ))
            conn.commit()
            return cursor.lastrowid

    def update_proxy(self, proxy_id, data):
        """更新代理，password 为占位符 ******** 或空时不更新"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            ignore_local = 1 if data.get('ignore_local') else 0
            set_clauses = ["name=?", "type=?", "host=?", "port=?", "username=?", "description=?", "ignore_local=?", "updated_at=CURRENT_TIMESTAMP"]
            params = [data['name'], data['type'], data['host'], data['port'], data.get('username'), data.get('description'), ignore_local]
            pw = data.get('password')
            if pw and pw != "********":
                set_clauses.append("password=?")
                params.append(self._encrypt(pw))
            params.append(proxy_id)
            cursor.execute(f"UPDATE proxies SET {', '.join(set_clauses)} WHERE id=?", tuple(params))
            conn.commit()

    def delete_proxy(self, proxy_id):
        """删除代理，并清除相关绑定"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 清除引用此代理的绑定
            settings = self.get_system_settings()
            for key in ['proxy_for_terminal', 'proxy_for_ai', 'proxy_for_skills']:
                if str(settings.get(key, '')) == str(proxy_id):
                    cursor.execute('UPDATE system_settings SET value = ? WHERE key = ?', ('', key))
            cursor.execute('DELETE FROM proxies WHERE id = ?', (proxy_id,))
            conn.commit()

    def get_proxy_bindings(self):
        """获取场景绑定：{ terminal: proxy_id, ai: proxy_id, skills: proxy_id }"""
        settings = self.get_system_settings()
        def _to_id(v):
            if not v or v == '0':
                return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        return {
            'terminal': _to_id(settings.get('proxy_for_terminal')),
            'ai': _to_id(settings.get('proxy_for_ai')),
            'skills': _to_id(settings.get('proxy_for_skills')),
        }

    def update_proxy_bindings(self, terminal=None, ai=None, skills=None):
        """更新场景绑定。None/0 表示解除绑定；传入后必须更新，否则解除勾选时旧值会残留"""
        def _to_val(x):
            if x is None or x == 0:
                return ''
            return str(int(x))
        # 始终更新：接口每次发送完整 bindings，null 表示解除绑定
        self.upsert_system_setting('proxy_for_terminal', _to_val(terminal))
        self.upsert_system_setting('proxy_for_ai', _to_val(ai))
        self.upsert_system_setting('proxy_for_skills', _to_val(skills))

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
                r['trigger_words'] = self._json_text_to_list(r.get('trigger_words'), [])
                r['scope_tags'] = self._json_text_to_list(r.get('scope_tags'), ['ops'])
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
            result['trigger_words'] = self._json_text_to_list(result.get('trigger_words'), [])
            result['scope_tags'] = self._json_text_to_list(result.get('scope_tags'), ['ops'])
            return result

    def get_skill_by_name(self, name):
        """按 name 查找技能"""
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM skills WHERE name = ?', (name,))
            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result['trigger_words'] = self._json_text_to_list(result.get('trigger_words'), [])
            result['scope_tags'] = self._json_text_to_list(result.get('scope_tags'), ['ops'])
            return result

    def add_skill(self, data):
        """创建技能"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            trigger_words = self._json_list_to_text(data.get('trigger_words'), [])
            scope_tags = self._json_list_to_text(data.get('scope_tags'), ['ops'])
            cursor.execute('''
                INSERT INTO skills (name, display_name, description, description_zh, source, source_url, skill_path, content, trigger_words, scope_tags, is_enabled, install_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                scope_tags,
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
            trigger_words = self._json_list_to_text(data.get('trigger_words'), [])
            scope_tags = self._json_list_to_text(data.get('scope_tags'), ['ops'])
            cursor.execute('''
                UPDATE skills SET
                    name=?, display_name=?, description=?, description_zh=?, source=?, source_url=?,
                    skill_path=?, content=?, trigger_words=?, scope_tags=?, is_enabled=?, install_count=?,
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
                scope_tags,
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

    def get_skills_for_scope(
        self,
        scope: str,
        device_type_value: str = "unknown",
        enabled_only: bool = True,
        filter_mode: str = "soft",
    ):
        """按 scope + 设备类型筛选技能（供 AI 对话注入）。"""
        mode = (filter_mode or "soft").strip().lower()
        if mode not in ("off", "soft", "strict"):
            mode = "soft"
        dt = (device_type_value or "").strip().lower()
        all_skills = self.get_all_skills(enabled_only=enabled_only)
        if not all_skills:
            return []
        all_types = self.get_all_device_types()
        dt_id_to_value = {int(t["id"]): str(t.get("value") or "").lower() for t in all_types}
        scope_key = (scope or "ops").strip().lower()
        scope_aliases = {
            "ops": {"ops"},
            "task": {"task", "evolution"},
            "plugin": {"plugin", "evolution"},
            "evolution": {"evolution", "task", "plugin"},
        }
        accepted_scopes = scope_aliases.get(scope_key, {scope_key})
        result = []
        for skill in all_skills:
            tags = {str(x).strip().lower() for x in (skill.get("scope_tags") or ["ops"]) if str(x).strip()}
            if not tags:
                tags = {"ops"}
            scope_match = any(tag in accepted_scopes for tag in tags)
            if mode == "strict" and not scope_match:
                continue
            if mode == "soft" and not scope_match:
                continue
            bound_ids = skill.get("bound_device_type_ids") or []
            bound_values = {dt_id_to_value.get(int(i), "") for i in bound_ids}
            bound_values.discard("")
            device_match = bool(dt and dt != "unknown" and dt in bound_values)
            if scope_key == "ops":
                # 为保持旧行为：ops 模式下仍要求设备类型绑定精确匹配
                if not bound_values:
                    continue
                if not device_match:
                    continue
            else:
                if bound_values and dt and dt != "unknown" and not device_match:
                    # 对话类场景若设备类型明确且不匹配，不注入
                    continue
            row = {
                "id": skill.get("id"),
                "name": skill.get("name"),
                "display_name": skill.get("display_name"),
                "content": skill.get("content") or "",
                "scope_tags": sorted(tags),
            }
            result.append(((2 if scope_match else 0) + (1 if device_match else 0), row))
        result.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in result]

    # --- 自进化任务（Phase A） ---
    def add_evolution_task(self, data):
        payload_json = data.get('payload_json')
        if payload_json is None:
            payload_json = json.dumps(data.get('payload', {}), ensure_ascii=False)
        elif not isinstance(payload_json, str):
            payload_json = json.dumps(payload_json, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO evolution_tasks (
                    title, description, source, task_type, scope, risk_level,
                    status, requires_approval, approval_status, max_retries,
                    retry_count, error_signature, error_repeat_count,
                    acceptance_criteria, rollback_plan, payload_json,
                    final_report, needs_human_action, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get('title'),
                data.get('description', ''),
                data.get('source', 'user'),
                data.get('task_type', 'fix'),
                data.get('scope', 'backend'),
                data.get('risk_level', 'low'),
                data.get('status', 'new'),
                int(data.get('requires_approval', 0)),
                data.get('approval_status', 'none'),
                int(data.get('max_retries', 100)),
                int(data.get('retry_count', 0)),
                data.get('error_signature'),
                int(data.get('error_repeat_count', 0)),
                data.get('acceptance_criteria', ''),
                data.get('rollback_plan', ''),
                payload_json or '{}',
                data.get('final_report'),
                int(data.get('needs_human_action', 0)),
                data.get('created_by', 'system'),
            ))
            conn.commit()
            return cursor.lastrowid

    def evolution_task_title_exists(self, title: str, exclude_task_id: Optional[int] = None) -> bool:
        norm = (title or "").strip()
        if not norm:
            return False
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if exclude_task_id is None:
                cursor.execute(
                    "SELECT 1 FROM evolution_tasks WHERE TRIM(title) = ? LIMIT 1",
                    (norm,),
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM evolution_tasks WHERE TRIM(title) = ? AND id != ? LIMIT 1",
                    (norm, int(exclude_task_id)),
                )
            return cursor.fetchone() is not None

    def get_all_evolution_tasks(self, status=None, limit=100, offset=0):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT * FROM evolution_tasks
                    WHERE status = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                ''', (status, limit, offset))
            else:
                cursor.execute('''
                    SELECT * FROM evolution_tasks
                    ORDER BY created_at DESC, id DESC
                    LIMIT ? OFFSET ?
                ''', (limit, offset))
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                try:
                    row['payload'] = json.loads(row.get('payload_json') or '{}')
                except Exception:
                    row['payload'] = {}
            return rows

    def get_evolution_task_by_id(self, task_id):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM evolution_tasks WHERE id = ?', (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data['payload'] = json.loads(data.get('payload_json') or '{}')
            except Exception:
                data['payload'] = {}
            return data

    def update_evolution_task(self, task_id, updates: dict):
        if not updates:
            return
        fields = []
        values = []
        for k, v in updates.items():
            if k == 'payload':
                k = 'payload_json'
                v = json.dumps(v or {}, ensure_ascii=False)
            elif k == 'payload_json' and not isinstance(v, str):
                v = json.dumps(v or {}, ensure_ascii=False)
            fields.append(f"{k} = ?")
            values.append(v)
        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.append(task_id)
        sql = f"UPDATE evolution_tasks SET {', '.join(fields)} WHERE id = ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(values))
            conn.commit()

    def delete_evolution_task(self, task_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM evolution_runs WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM evolution_experiences WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM evolution_failure_reports WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM evolution_tasks WHERE id = ?", (task_id,))
            conn.commit()

    def add_evolution_run(self, task_id, run_status, trigger_type='manual', detail='', result_json=None, operator='system'):
        if result_json is None:
            result_json = {}
        if not isinstance(result_json, str):
            result_json = json.dumps(result_json, ensure_ascii=False)
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA table_info(evolution_runs)")
                run_cols = [c[1] for c in cursor.fetchall()]
                if 'operator' not in run_cols:
                    cursor.execute("ALTER TABLE evolution_runs ADD COLUMN operator TEXT DEFAULT 'system'")
                conn.commit()
        except Exception:
            pass
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO evolution_runs (task_id, trigger_type, run_status, detail, result_json, operator)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (task_id, trigger_type, run_status, detail, result_json, operator))
            conn.commit()
            return cursor.lastrowid

    def get_runs_by_task_id(self, task_id, limit=50, order='desc'):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            order_sql = 'DESC' if (order or 'desc').lower() != 'asc' else 'ASC'
            cursor.execute('''
                SELECT * FROM evolution_runs
                WHERE task_id = ?
                ORDER BY id {order_sql}
                LIMIT ?
            '''.format(order_sql=order_sql), (task_id, limit))
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                try:
                    row['result'] = json.loads(row.get('result_json') or '{}')
                except Exception:
                    row['result'] = {}
            return rows

    def clear_evolution_data(self, full: bool = False):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM evolution_runs')
            cursor.execute('DELETE FROM evolution_tasks')
            if full:
                cursor.execute('DELETE FROM evolution_experiences')
                cursor.execute('DELETE FROM evolution_failure_reports')
                cursor.execute('DELETE FROM schema_migrations')
            conn.commit()

    # --- 插件注册与调度规则（Phase C） ---
    def upsert_plugin_registry(self, data: dict):
        manifest = data.get("manifest_json")
        if manifest is None:
            manifest = json.dumps(data.get("manifest", {}), ensure_ascii=False)
        elif not isinstance(manifest, str):
            manifest = json.dumps(manifest, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO plugin_registry (
                    plugin_id, name, version, status, runtime, manifest_json, install_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    status=excluded.status,
                    runtime=excluded.runtime,
                    manifest_json=excluded.manifest_json,
                    install_path=excluded.install_path,
                    updated_at=CURRENT_TIMESTAMP
            ''', (
                data.get("plugin_id"),
                data.get("name", data.get("plugin_id")),
                data.get("version", "0.1.0"),
                data.get("status", "installed"),
                data.get("runtime", "python"),
                manifest or "{}",
                data.get("install_path"),
            ))
            conn.commit()

    def get_all_plugins(self, status: Optional[str] = None):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM plugin_registry WHERE status = ? ORDER BY updated_at DESC, id DESC",
                    (status,),
                )
            else:
                cursor.execute("SELECT * FROM plugin_registry ORDER BY updated_at DESC, id DESC")
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["manifest"] = json.loads(row.get("manifest_json") or "{}")
                except Exception:
                    row["manifest"] = {}
            return rows

    def get_plugin_by_plugin_id(self, plugin_id: str):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM plugin_registry WHERE plugin_id = ?", (plugin_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["manifest"] = json.loads(data.get("manifest_json") or "{}")
            except Exception:
                data["manifest"] = {}
            return data

    def set_plugin_status(self, plugin_id: str, status: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE plugin_registry SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE plugin_id = ?",
                (status, plugin_id),
            )
            conn.commit()

    def delete_plugin_registry(self, plugin_id: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM plugin_registry WHERE plugin_id = ?", (plugin_id,))
            conn.commit()

    def replace_plugin_schedules(self, plugin_id: str, schedules: list):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scheduled_jobs WHERE plugin_id = ?", (plugin_id,))
            for item in schedules or []:
                cursor.execute('''
                    INSERT INTO scheduled_jobs (
                        job_name, source_type, task_template_key, plugin_id,
                        cron_expr, timezone, window_start, window_end,
                        enabled, max_concurrent, max_retries, policy_overrides_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item.get("name") or f"{plugin_id}-schedule",
                    "plugin_manifest",
                    item.get("task_template_key", ""),
                    plugin_id,
                    item.get("cron", "0 */5 * * * *"),
                    item.get("timezone", "Asia/Shanghai"),
                    item.get("window_start"),
                    item.get("window_end"),
                    int(item.get("enabled", True)),
                    int(item.get("max_concurrent", 1)),
                    int(item.get("max_retries", 100)),
                    json.dumps(item.get("policy_overrides", {}), ensure_ascii=False),
                ))
            conn.commit()

    def get_schedules_by_plugin(self, plugin_id: str):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM scheduled_jobs WHERE plugin_id = ? ORDER BY id DESC",
                (plugin_id,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["policy_overrides"] = json.loads(row.get("policy_overrides_json") or "{}")
                except Exception:
                    row["policy_overrides"] = {}
            return rows

    def get_evolution_scheduler_config(self):
        settings = self.get_system_settings()
        return {
            "enabled": (settings.get("evolution_scheduler_enabled", "1") == "1"),
            "interval_sec": int(settings.get("evolution_scheduler_interval_sec", "30") or "30"),
            "max_tasks_per_tick": int(settings.get("evolution_scheduler_max_tasks_per_tick", "3") or "3"),
            "retry_delay_sec": int(settings.get("evolution_retry_delay_sec", "60") or "60"),
        }

    def update_evolution_scheduler_config(self, data: dict):
        if "enabled" in data:
            self.update_system_setting("evolution_scheduler_enabled", "1" if data["enabled"] else "0")
        if "interval_sec" in data:
            self.update_system_setting("evolution_scheduler_interval_sec", str(max(1, int(data["interval_sec"]))))
        if "max_tasks_per_tick" in data:
            self.update_system_setting("evolution_scheduler_max_tasks_per_tick", str(max(1, int(data["max_tasks_per_tick"]))))
        if "retry_delay_sec" in data:
            self.update_system_setting("evolution_retry_delay_sec", str(max(0, int(data["retry_delay_sec"]))))

    def get_schedulable_evolution_tasks(self, max_tasks: int = 3):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM evolution_tasks
                WHERE (
                    status = 'approved'
                    OR (status = 'failed' AND retry_count < max_retries)
                )
                ORDER BY id ASC
                LIMIT ?
            ''', (max_tasks,))
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["payload"] = json.loads(row.get("payload_json") or "{}")
                except Exception:
                    row["payload"] = {}
            return rows

    # --- Phase D: 经验库 / 报告 / 迁移审计 ---
    def add_evolution_experience(self, data: dict):
        raw = data.get("raw_json", data.get("raw", {}))
        if not isinstance(raw, str):
            raw = json.dumps(raw or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO evolution_experiences (
                    task_id, error_category, error_signature,
                    summary, action_suggestion, is_resolved, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                data.get("task_id"),
                data.get("error_category", "unknown"),
                data.get("error_signature"),
                data.get("summary", ""),
                data.get("action_suggestion", ""),
                int(data.get("is_resolved", 0)),
                raw or "{}",
            ))
            conn.commit()
            return cursor.lastrowid

    def get_evolution_experiences(self, limit: int = 50):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM evolution_experiences ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["raw"] = json.loads(row.get("raw_json") or "{}")
                except Exception:
                    row["raw"] = {}
            return rows

    def add_evolution_failure_report(self, data: dict):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO evolution_failure_reports (
                    task_id, report_title, report_markdown, notify_channel, notify_status
                ) VALUES (?, ?, ?, ?, ?)
            ''', (
                int(data.get("task_id")),
                data.get("report_title", "无法修复报告"),
                data.get("report_markdown", ""),
                data.get("notify_channel", "ui"),
                data.get("notify_status", "pending"),
            ))
            conn.commit()
            return cursor.lastrowid

    def update_evolution_failure_report_notify_status(self, report_id: int, status: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE evolution_failure_reports SET notify_status = ? WHERE id = ?",
                (status, int(report_id)),
            )
            conn.commit()

    def get_evolution_failure_reports(self, limit: int = 50):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM evolution_failure_reports ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            return [dict(r) for r in cursor.fetchall()]

    def add_schema_migration_record(self, data: dict):
        detail = data.get("detail_json", data.get("detail", {}))
        if not isinstance(detail, str):
            detail = json.dumps(detail or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO schema_migrations (
                    migration_name, checksum, status, applied_by, detail_json, rolled_back_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.get("migration_name", "unnamed_migration"),
                data.get("checksum", ""),
                data.get("status", "applied"),
                data.get("applied_by", "system"),
                detail or "{}",
                data.get("rolled_back_at"),
            ))
            conn.commit()
            return cursor.lastrowid

    def get_schema_migrations(self, limit: int = 50):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM schema_migrations ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
            rows = [dict(r) for r in cursor.fetchall()]
            for row in rows:
                try:
                    row["detail"] = json.loads(row.get("detail_json") or "{}")
                except Exception:
                    row["detail"] = {}
            return rows
