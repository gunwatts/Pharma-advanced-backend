"""Structured JSON logging with secret redaction."""

import json
import logging
import re
from datetime import datetime, timezone

SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|client_secret|refresh_token)"
               r"(\"?\s*[:=]\s*\"?)([^\s\",;&]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
]


def redact(text):
    if not isinstance(text, str):
        return text
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed log call
            return True
        record.msg = redact(message)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {"message", "asctime"}

    def format(self, record):
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = redact(value) if isinstance(value, str) else value
                except TypeError:
                    payload[key] = str(value)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)
