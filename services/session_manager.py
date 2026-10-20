from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager

from config import CACHE_ROOT, CACHE_EXPIRATION_HOURS

# One application worker; child matching processes never change session.json.
_lock = threading.RLock()
_stop = threading.Event()
_thread = None


def validate_id(value):
    if not re.fullmatch(r"[0-9a-f]{32}", str(value)):
        raise ValueError("Invalid session or job identifier.")
    return value


def session_dir(session_id):
    return CACHE_ROOT / ("session_" + validate_id(session_id))


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_session():
    with _lock:
        payload = {"session_id": uuid.uuid4().hex, "created_at": time.time(), "updated_at": time.time()}
        root = session_dir(payload["session_id"])
        for directory in ("uploads", "working", "exports"):
            (root / directory).mkdir(parents=True)
        save_session(payload)
        return payload


def load_session(session_id):
    return json.loads((session_dir(session_id) / "session.json").read_text(encoding="utf-8"))


def save_session(payload):
    payload["updated_at"] = time.time()
    atomic_json(session_dir(payload["session_id"]) / "session.json", payload)


@contextmanager
def edit_session(session_id):
    from services.job_manager import is_busy
    with _lock:
        if is_busy(session_id):
            raise ValueError("This session has a queued or running job. Wait until it finishes.")
        yield


def remove_session(session_id):
    with edit_session(session_id):
        shutil.rmtree(session_dir(session_id))


def cleanup_expired():
    from services.job_manager import is_busy
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - CACHE_EXPIRATION_HOURS * 3600
    with _lock:
        for root in CACHE_ROOT.glob("session_*"):
            try:
                sid = validate_id(root.name.removeprefix("session_"))
                if root.is_symlink() or is_busy(sid):
                    continue
                payload = load_session(sid)
                if float(payload["updated_at"]) < cutoff:
                    shutil.rmtree(root)
            except (OSError, ValueError, KeyError):
                continue


def start_cleanup_thread():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    def loop():
        while not _stop.wait(300):
            cleanup_expired()
    _thread = threading.Thread(target=loop, daemon=True, name="crossmatch-cleanup")
    _thread.start()


def stop_cleanup_thread():
    _stop.set()
    if _thread:
        _thread.join(timeout=2)
