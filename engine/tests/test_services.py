import time

from ir_engine.runtime.services import BackgroundService, ServiceManager


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class Counter(BackgroundService):
    name = "counter"
    interval_s = 0.05

    def __init__(self):
        super().__init__(db=None)
        self.ticks = 0

    def tick(self) -> bool:
        self.ticks += 1
        return self.ticks < 3


class Broken(BackgroundService):
    name = "broken"
    interval_s = 0.05
    error_backoff_s = 0.05

    def tick(self) -> bool:
        raise ValueError("boom")


class Pending(BackgroundService):
    name = "pending"
    interval_s = 0.05

    def tick(self) -> bool:
        raise NotImplementedError


def test_lifecycle_pause_resume_stop():
    svc = Counter()
    manager = ServiceManager([svc])
    manager.start_all()
    assert wait_for(lambda: svc.ticks >= 4)
    svc.pause()
    assert wait_for(lambda: svc.state == "paused")
    paused_at = svc.ticks
    time.sleep(0.2)
    assert svc.ticks <= paused_at + 1
    svc.resume()
    assert wait_for(lambda: svc.ticks > paused_at + 1)
    manager.stop_all()
    assert svc.state == "stopped"
    assert manager.status()["counter"]["state"] == "stopped"


def test_errors_are_reported_and_not_implemented_is_quiet():
    broken, pending = Broken(db=None), Pending(db=None)
    manager = ServiceManager([broken, pending])
    manager.start_all()
    assert wait_for(lambda: broken.last_error == "ValueError: boom")
    assert wait_for(lambda: pending.last_tick is not None)
    assert pending.last_error is None and pending.state == "running"
    manager.stop_all()
