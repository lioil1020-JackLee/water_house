"""Writable diagnostics shared by the GUI and its external watchdog."""

from __future__ import annotations

import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any

APP_VERSION = "4.1.1"
_fault_stream = None


def get_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "WaterHouse"


def get_session_id() -> str:
    """Keep local acknowledgements and single-instance locks per desktop session."""
    if sys.platform == "win32":
        import ctypes
        session = ctypes.c_ulong()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise ctypes.WinError()
        return str(session.value)
    return "desktop"


class AlarmEventFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = dict(getattr(record, "alarm_event", {}))
        payload.setdefault("event", record.getMessage())
        payload.setdefault("timestamp", record.created)
        payload.setdefault("pid", os.getpid())
        payload.setdefault("version", APP_VERSION)
        return json.dumps(payload, ensure_ascii=False, default=str)


def record_alarm_event(event: str, **fields: Any) -> None:
    """Retain received/cleared/acknowledged/displayed events on each PC."""
    payload = {**fields, "event": event, "timestamp": time.time(), "pid": os.getpid()}
    logging.getLogger("water_house.alarms").info(
        "%s %s", event, json.dumps(fields, ensure_ascii=False, default=str),
        extra={"alarm_event": payload},
    )


def setup_runtime_logging(log_dir: Path | None = None) -> Path:
    global _fault_stream
    directory = Path(log_dir) if log_dir is not None else get_data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(threadName)s %(name)s %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        directory / "water_house.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    event_handler = RotatingFileHandler(
        directory / "alarm_events.jsonl", maxBytes=2_000_000, backupCount=10, encoding="utf-8"
    )
    event_handler.setFormatter(AlarmEventFormatter())
    logging.getLogger("water_house.alarms").addHandler(event_handler)
    # Keep routine transport internals out of the event audit trail and main log.
    logging.getLogger("asyncua").setLevel(logging.WARNING)

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logging.critical("未捕捉例外", exc_info=(exc_type, exc_value, exc_traceback))

    def handle_thread_exception(args):
        logging.critical(
            "執行緒未捕捉例外 (%s)", getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception
    _fault_stream = open(directory / "water_house_fault.log", "a", encoding="utf-8")
    faulthandler.enable(_fault_stream)
    logging.info("water_house 啟動 version=%s PID=%s", APP_VERSION, os.getpid())
    return directory


def atomic_json_write(path: Path, payload: dict) -> None:
    """Never expose a half-written heartbeat/state to another process."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory replacement is atomic, and a PID suffix avoids collisions.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def heartbeat_payload(window, now: float | None = None) -> dict:
    client = getattr(window, "opcua_client", None)
    worker_alive = bool(client is not None and client.isRunning())
    health = client.health_snapshot() if client is not None else {}
    return {
        "pid": os.getpid(),
        "executable_path": str(Path(sys.executable).resolve()),
        "version": APP_VERSION,
        "updated_at": time.time() if now is None else now,
        "worker_alive": worker_alive,
        "last_data_at": health.get("last_data_at"),
        "monitoring_healthy": bool(health.get("monitoring_healthy", False)),
        "recovery_active": bool(getattr(window, "monitoring_recovery_active", False)),
    }


def write_heartbeat(log_dir: Path, window) -> bool:
    try:
        payload = heartbeat_payload(window)
        # The per-process file is authoritative across RDP/Windows sessions.
        atomic_json_write(log_dir / f"water_house.{os.getpid()}.heartbeat", payload)
        atomic_json_write(log_dir / "water_house.heartbeat", payload)
        return True
    except Exception:
        logging.exception("無法寫入監控 heartbeat")
        return False
