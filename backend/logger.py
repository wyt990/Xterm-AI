import logging
import os
from logging.handlers import RotatingFileHandler
from database import Database
from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")

class Logger:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
            cls._instance._setup_logger()
        return cls._instance

    def _setup_logger(self):
        # 暂时使用环境变量或默认值，之后由 main.py 触发动态重载
        db_path = os.getenv("DB_PATH", "../config/xterm.db")
        db = Database(db_path)
        settings = db.get_system_settings()
        
        log_enabled = settings.get("log_enabled") == "1"
        log_path = settings.get("log_path", "../logs")
        log_max_size = int(settings.get("log_max_size", 10)) * 1024 * 1024
        log_backup_count = int(settings.get("log_backup_count", 10))
        log_level_str = settings.get("log_level", "INFO").upper()
        
        # 映射等级
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "OFF": logging.CRITICAL + 1
        }
        level = levels.get(log_level_str, logging.INFO)

        self.logger = logging.getLogger("xterm_ai")
        self.logger.setLevel(level)
        
        # 清除旧的 handlers
        if self.logger.handlers:
            for handler in self.logger.handlers:
                self.logger.removeHandler(handler)

        if log_enabled and log_level_str != "OFF":
            if not os.path.exists(log_path):
                os.makedirs(log_path, exist_ok=True)
            
            log_file = os.path.join(log_path, "xterm.log")
            handler = RotatingFileHandler(log_file, maxBytes=log_max_size, backupCount=log_backup_count, encoding='utf-8')
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] [%(name)s] %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            
        # 始终添加控制台输出以便调试
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        self.logger.addHandler(console_handler)

    def reload(self):
        self._setup_logger()

    def info(self, module, msg):
        self.logger.info(f"[{module}] {msg}")

    def error(self, module, msg):
        self.logger.error(f"[{module}] {msg}")

    def debug(self, module, msg):
        self.logger.debug(f"[{module}] {msg}")

    def warning(self, module, msg):
        self.logger.warning(f"[{module}] {msg}")

# 创建单例对象
app_logger = Logger()
