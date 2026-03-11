import queue
import threading
from typing import Callable, Dict


class EvolutionTaskQueue:
    def __init__(self, execute_fn: Callable[[int], None], max_workers: int = 1, max_size: int = 100):
        self.execute_fn = execute_fn
        self.max_workers = max(1, int(max_workers))
        self.max_size = max(1, int(max_size))
        self._queue: "queue.Queue[int]" = queue.Queue(maxsize=self.max_size)
        self._workers = []
        self._running = False
        self._lock = threading.Lock()
        self._running_count = 0
        self._processed_count = 0
        self._failed_count = 0

    def start(self):
        if self._running:
            return
        self._running = True
        self._workers = []
        for idx in range(self.max_workers):
            worker = threading.Thread(target=self._worker_loop, name=f"evo-queue-{idx}", daemon=True)
            worker.start()
            self._workers.append(worker)

    def stop(self):
        if not self._running:
            return
        self._running = False
        # 发送停止哨兵
        for _ in self._workers:
            try:
                self._queue.put_nowait(-1)
            except queue.Full:
                break
        for worker in self._workers:
            worker.join(timeout=1.0)
        self._workers = []

    def enqueue(self, task_id: int):
        if not self._running:
            self.start()
        self._queue.put_nowait(task_id)

    def _worker_loop(self):
        while self._running:
            try:
                task_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if task_id == -1:
                self._queue.task_done()
                continue
            with self._lock:
                self._running_count += 1
            try:
                self.execute_fn(task_id)
                with self._lock:
                    self._processed_count += 1
            except Exception:
                with self._lock:
                    self._failed_count += 1
            finally:
                with self._lock:
                    self._running_count -= 1
                self._queue.task_done()

    def status(self) -> Dict[str, int]:
        with self._lock:
            return {
                "enabled": 1 if self._running else 0,
                "queued": self._queue.qsize(),
                "running": self._running_count,
                "workers": self.max_workers,
                "processed": self._processed_count,
                "failed": self._failed_count,
                "max_size": self.max_size,
            }

