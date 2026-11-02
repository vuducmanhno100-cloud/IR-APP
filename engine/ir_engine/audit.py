import psycopg
from psycopg.types.json import Jsonb

ACTOR_ANALYST = "analyst"
ACTOR_SYSTEM = "system"
SECRET_KEYS = {"api_key", "apikey", "token", "password", "secret", "authorization"}


def _contains_secret(value) -> bool:
    if isinstance(value, dict):
        return any(str(k).lower() in SECRET_KEYS or _contains_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(v) for v in value)
    return False


def record(conn: psycopg.Connection, action: str, target: str | None = None, detail: dict | None = None, actor: str = ACTOR_ANALYST) -> int:
    if _contains_secret(detail):
        raise ValueError("audit detail must not contain secrets")
    row = conn.execute(
        "INSERT INTO audit_log (actor, action, target, detail) VALUES (%s, %s, %s, %s) RETURNING id",
        (actor, action, target, Jsonb(detail) if detail is not None else None),
    ).fetchone()
    return row["id"] if isinstance(row, dict) else row[0]
