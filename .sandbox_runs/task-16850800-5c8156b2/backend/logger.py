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
        log_path, log_enabled, log_max_size, log_backup_count, log_level_str = self._resolve_logging_config()
        logger_impl = self._create_base_logger(log_level_str)
        self._configure_file_handler(logger_impl, log_path, log_enabled, log_level_str, log_max_size, log_backup_count)
        self._configure_console_handler(logger_impl)
        self._logger_impl = logger_impl

    def _resolve_logging_config(self):
        """解析日志配置，优先使用打包注入的 LOG_PATH。"""
        log_path = os.getenv("LOG_PATH")
        if log_path:
            return log_path, True, 10 * 1024 * 1024, 10, "INFO"

        db_path = os.getenv("DB_PATH", "../config/xterm.db")
        db = Database(db_path)
        settings = db.get_system_settings()
        return (
            settings.get("log_path", "../logs"),
            settings.get("log_enabled") == "1",
            int(settings.get("log_max_size", 10)) * 1024 * 1024,
            int(settings.get("log_backup_count", 10)),
            settings.get("log_level", "INFO").upper(),
        )

    def _create_base_logger(self, log_level_str):
        levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "OFF": logging.CRITICAL + 1,
        }
        level = levels.get(log_level_str, logging.INFO)
        logger_impl = logging.getLogger("xterm_ai")
        logger_impl.setLevel(level)
        if logger_impl.handlers:
            for handler in logger_impl.handlers:
                logger_impl.removeHandler(handler)
        return logger_impl

    def _configure_file_handler(self, logger_impl, log_path, log_enabled, log_level_str, log_max_size, log_backup_count):
        if not (log_enabled and log_level_str != "OFF"):
            return
        try:
            resolved_log_path = self._resolve_log_path(log_path)
            if not os.path.exists(resolved_log_path):
                os.makedirs(resolved_log_path, exist_ok=True)
            log_file = os.path.join(resolved_log_path, "xterm.log")
            handler = RotatingFileHandler(log_file, maxBytes=log_max_size, backupCount=log_backup_count, encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
            handler.setFormatter(formatter)
            logger_impl.addHandler(handler)
            logger_impl.info("[xterm_ai] Logger initialized, log_path=%s", resolved_log_path)
        except Exception as e:
            self._write_startup_log(log_path, e)

    def _configure_console_handler(self, logger_impl):
        try:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger_impl.addHandler(console_handler)
        except (ValueError, OSError):
            pass

    def _resolve_log_path(self, log_path):
        if log_path and not os.path.isabs(log_path):
            base_dir = os.path.dirname(os.path.abspath(__file__))
            return os.path.normpath(os.path.join(base_dir, log_path))
        return log_path or "."

    def _write_startup_log(self, log_path, err):
        try:
            startup_log = os.path.join(os.getenv("LOG_PATH", log_path or "."), "startup.log")
            with open(startup_log, "a", encoding="utf-8") as f:
                f.write(f"[Logger] xterm.log 创建失败: {err}\n")
        except Exception:
            pass

    def reload(self):
        self._setup_logger()

    def info(self, module, msg):
        self._logger_impl.info(f"[{module}] {msg}")

    def error(self, module, msg):
        self._logger_impl.error(f"[{module}] {msg}")

    def debug(self, module, msg):
        self._logger_impl.debug(f"[{module}] {msg}")

    def warning(self, module, msg):
        self._logger_impl.warning(f"[{module}] {msg}")

# 创建单例对象
app_logger = Logger()
