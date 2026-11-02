import logging
import re
import sys
from pathlib import Path

PATTERNS = [
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)(apikey|bearer|basic)\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)((?:api_key|apikey|token|password|secret)['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"),
]


def redact(text: str) -> str:
    text = PATTERNS[0].sub(r"\1\2 ***", text)
    return PATTERNS[1].sub(r"\1***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


def setup(level: str = "INFO", log_dir: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "engine.log", encoding="utf-8"))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    for noisy in ("elastic_transport", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
