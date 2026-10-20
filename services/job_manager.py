from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from config import BASE_DIR, CACHE_ROOT, MAX_CONCURRENT_JOBS
from services.session_manager import atomic_json, load_session, save_session, session_dir, validate_id

_lock = threading.RLock()
_active = set()
_executor = None


def is_busy(session_id):
    with _lock:
        return session_id in _active


def job_dir(session_id, job_id):
    return session_dir(session_id) / "working" / validate_id(job_id)


def get_job(session_id, job_id):
    return json.loads((job_dir(session_id, job_id) / "job.json").read_text(encoding="utf-8"))


def read_log_tail(session_id, job_id, lines=80):
    path = job_dir(session_id, job_id) / "engine.log"
    if not path.exists():
        return ""
    # Read only the end of the file; polling cost does not grow with the job log.
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - 32000))
        return "\n".join(deque(handle.read().decode("utf-8", errors="replace").splitlines(), maxlen=lines))


def recover_interrupted_jobs():
    for path in CACHE_ROOT.glob("session_*/working/*/job.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("state") in {"queued", "running"}:
                data.update(state="failed", message="Server restarted before this job finished. Run it again.", finished_at=time.time())
                atomic_json(path, data)
        except (OSError, ValueError):
            continue


def _run(session_id, job_id):
    root = job_dir(session_id, job_id)
    try:
        with (root / "engine.log").open("a", encoding="utf-8") as log:
            result = subprocess.run(
                [sys.executable, "-m", "services.worker", str(root / "request.json")],
                cwd=BASE_DIR, stdout=log, stderr=subprocess.STDOUT, check=False,
            )
        status = get_job(session_id, job_id)
        if result.returncode or status["state"] != "completed":
            status.update(state="failed", finished_at=time.time(),
                          message=status.get("message") if status["state"] == "failed" else "Processing stopped. See the processing log.")
            atomic_json(root / "job.json", status)
    except Exception as exc:
        status = get_job(session_id, job_id)
        status.update(state="failed", message=f"Could not run processing: {exc}", finished_at=time.time())
        atomic_json(root / "job.json", status)
    finally:
        try:
            # Keep completed exports for the full retention interval after completion.
            stored_session = load_session(session_id)
            save_session(stored_session)
            # Track the terminal result even if the user closes the browser before
            # polling. Audit DB is separate from temporary beneficiary data.
            try:
                from services import auth_manager as audit
                audit.record_job_outcome_once(None,
                    {"username": stored_session.get("owner_username", "system")},
                    get_job(session_id, job_id), session_id)
            except Exception:
                # An audit storage failure must not rewrite completed matching results.
                import traceback
                with (root / "engine.log").open("a", encoding="utf-8") as audit_log:
                    audit_log.write("\nAudit completion record failed:\n" + traceback.format_exc())
        finally:
            with _lock:
                _active.discard(session_id)



def start_job(session_id, **request):
    global _executor
    with _lock:
        if session_id in _active:
            raise ValueError("This session already has a queued or running job.")
        job_id = uuid.uuid4().hex
        root = job_dir(session_id, job_id)
        root.mkdir(parents=True)
        exports = session_dir(session_id) / "exports" / job_id
        exports.mkdir(parents=True)
        request.update(session_id=session_id, job_id=job_id, output_folder=str(exports))
        # Each job owns immutable mapping copies, including when the next job is submitted.
        for side in ("a", "b"):
            value = request.get(f"mapping_{side}_path")
            if value:
                from pathlib import Path
                saved = root / f"mapping_{side}.json"
                saved.write_bytes(Path(value).read_bytes())
                request[f"mapping_{side}_path"] = str(saved)
        status = {"session_id": session_id, "job_id": job_id, "state": "queued",
                  "created_at": time.time(), "message": "Queued — waiting for an available processing slot."}
        atomic_json(root / "job.json", status)
        atomic_json(root / "request.json", request)
        _active.add(session_id)
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_JOBS, thread_name_prefix="crossmatch-job")
        try:
            _executor.submit(_run, session_id, job_id)
        except Exception:
            _active.discard(session_id)
            status.update(state="failed", message="The server is shutting down. Try again after restart.")
            atomic_json(root / "job.json", status)
            raise
        return status


def shutdown_jobs():
    global _executor
    if _executor:
        _executor.shutdown(wait=True)
        _executor = None
