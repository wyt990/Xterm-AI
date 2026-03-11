import threading
import time
from typing import Callable, Dict


class EvolutionScheduler:
    def __init__(self, config_getter: Callable[[], Dict], run_once_fn: Callable[[], Dict]):
        self.config_getter = config_getter
        self.run_once_fn = run_once_fn
        self._running = False
        self._thread = None
        self._last_result = {
            "picked": 0,
            "enqueued": 0,
            "skipped": 0,
            "enabled": False,
            "at": None,
        }

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="evolution-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _loop(self):
        while self._running:
            cfg = self.config_getter()
            enabled = bool(cfg.get("enabled", True))
            interval_sec = max(1, int(cfg.get("interval_sec", 30)))
            if enabled:
                try:
                    self._last_result = self.run_once_fn()
                    self._last_result["enabled"] = True
                    self._last_result["at"] = int(time.time())
                except Exception:
                    self._last_result = {
                        "picked": 0,
                        "enqueued": 0,
                        "skipped": 0,
                        "enabled": True,
                        "at": int(time.time()),
                    }
            else:
                self._last_result = {
                    "picked": 0,
                    "enqueued": 0,
                    "skipped": 0,
                    "enabled": False,
                    "at": int(time.time()),
                }
            for _ in range(interval_sec * 10):
                if not self._running:
                    break
                time.sleep(0.1)

    def status(self):
        return {
            "running": self._running,
            "last_result": self._last_result,
            "config": self.config_getter(),
        }

