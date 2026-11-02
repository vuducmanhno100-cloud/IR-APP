import os
import secrets
import threading

SERVICE = "IRDesktop"
PG_PASSWORD = "postgres-superuser"

_shared = None
_shared_lock = threading.Lock()


def elk_ref(cluster_key: str) -> str:
    return f"elk-api-key:{cluster_key}"


class KeyringStore:
    def __init__(self, service: str = SERVICE):
        import keyring

        self._keyring = keyring
        self.service = service

    def get(self, name: str) -> str | None:
        return self._keyring.get_password(self.service, name)

    def set(self, name: str, value: str) -> None:
        self._keyring.set_password(self.service, name, value)

    def delete(self, name: str) -> None:
        try:
            self._keyring.delete_password(self.service, name)
        except self._keyring.errors.PasswordDeleteError:
            pass


class MemoryStore:
    def __init__(self):
        self._values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


def default_store():
    if os.environ.get("IR_SECRET_BACKEND") == "memory":
        return MemoryStore()
    return KeyringStore()


def shared_store():
    """One store per process, so the API and the sync worker see the same keys (matters for MemoryStore)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = default_store()
        return _shared


def get_or_create(store, name: str) -> str:
    value = store.get(name)
    if value is None:
        value = secrets.token_urlsafe(32)
        store.set(name, value)
    return value
