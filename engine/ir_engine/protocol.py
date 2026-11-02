import json
import sys
import threading
from dataclasses import dataclass
from typing import Callable, TextIO

from . import __version__
from .contracts import CONTRACT_VERSION


class HandshakeError(ValueError):
    pass


@dataclass(frozen=True)
class Handshake:
    token: str
    data_dir: str | None = None
    log_level: str = "INFO"


def read_handshake(stream: TextIO = sys.stdin) -> Handshake:
    line = stream.readline()
    if not line:
        raise HandshakeError("stdin closed before handshake")
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise HandshakeError("handshake is not JSON") from exc
    token = data.get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise HandshakeError("token must be a string of at least 32 characters")
    return Handshake(token=token, data_dir=data.get("data_dir"), log_level=data.get("log_level", "INFO"))


def emit(event: str, stream: TextIO = sys.stdout, **fields) -> None:
    stream.write(json.dumps({"event": event, **fields}) + "\n")
    stream.flush()


def emit_ready(port: int, stream: TextIO = sys.stdout) -> None:
    emit("ready", stream, port=port, version=__version__, contract_version=CONTRACT_VERSION)


def emit_error(code: str, message: str, stream: TextIO = sys.stdout) -> None:
    emit("error", stream, code=code, message=message)


def watch_eof(on_eof: Callable[[], None], stream: TextIO = sys.stdin) -> threading.Thread:
    def run():
        while stream.readline():
            pass
        on_eof()

    thread = threading.Thread(target=run, name="stdin-watch", daemon=True)
    thread.start()
    return thread
