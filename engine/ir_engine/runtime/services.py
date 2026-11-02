import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("ir_engine.services")


class BackgroundService:
    name = "service"
    interval_s: float = 5.0
    error_backoff_s: float = 30.0

    def __init__(self, db):
        self.db = db
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._warned_not_implemented = False
        self.last_tick: datetime | None = None
        self.last_error: str | None = None

    def tick(self) -> bool:
        raise NotImplementedError

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"svc-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        self._stop.set()
        self._resume.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def wake(self) -> None:
        """Start the next tick now instead of after interval_s (used by run-now, bootstrap, retry)."""
        self._wake.set()

    def should_interrupt(self) -> bool:
        """Long ticks check this between batches so pause and shutdown take effect after the current batch."""
        return self._stop.is_set() or not self._resume.is_set()

    @property
    def state(self) -> str:
        if not (self._thread and self._thread.is_alive()):
            return "stopped"
        return "running" if self._resume.is_set() else "paused"

    def status(self) -> dict:
        return {
            "state": self.state,
            "last_tick": self.last_tick.isoformat().replace("+00:00", "Z") if self.last_tick else None,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            self._resume.wait()
            if self._stop.is_set():
                break
            delay = self.interval_s
            try:
                worked = self.tick()
                self.last_error = None
                if worked:
                    delay = 0
            except NotImplementedError:
                if not self._warned_not_implemented:
                    log.info("%s: not implemented yet", self.name)
                    self._warned_not_implemented = True
            except Exception as exc:
                log.exception("%s tick failed", self.name)
                self.last_error = f"{type(exc).__name__}: {exc}"
                delay = self.error_backoff_s
            self.last_tick = datetime.now(timezone.utc)
            if delay:
                self._wake.wait(delay)
                self._wake.clear()
            else:
                time.sleep(0)


class ServiceManager:
    def __init__(self, services: list[BackgroundService] | None = None):
        self._services = {s.name: s for s in services or []}

    def get(self, name: str) -> BackgroundService | None:
        return self._services.get(name)

    def start_all(self) -> None:
        for s in self._services.values():
            s.start()

    def stop_all(self) -> None:
        for s in self._services.values():
            s.stop()

    def status(self) -> dict:
        return {name: s.status() for name, s in self._services.items()}
