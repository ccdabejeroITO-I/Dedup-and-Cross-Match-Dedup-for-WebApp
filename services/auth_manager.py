"""Local accounts and audit history. Beneficiary rows are never stored here."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

from config import DATABASE_ROOT, AUTH_SESSION_HOURS

COOKIE_NAME = "crossmatch_session"
# A new schema name protects databases from older packages with unknown schemas.
DB_PATH = DATABASE_ROOT / "crossmatch_web_v631.db"


@contextmanager
def _connect():
    DATABASE_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_auth_db():
    with _connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
          CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')),
            is_active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS login_sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at REAL NOT NULL, ip TEXT NOT NULL, agent TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS login_attempts (
            username TEXT NOT NULL, ip TEXT NOT NULL, attempted_at REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS attempt_time ON login_attempts(attempted_at);
          CREATE TABLE IF NOT EXISTS initial_setup_attempts (
            ip TEXT NOT NULL, attempted_at REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS initial_setup_attempt_time ON initial_setup_attempts(attempted_at);
          CREATE TABLE IF NOT EXISTS registration_requests (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
            requested_at REAL NOT NULL, reviewed_at REAL, reviewed_by TEXT);
          CREATE INDEX IF NOT EXISTS registration_status_time ON registration_requests(status, requested_at);
          CREATE TABLE IF NOT EXISTS registration_attempts (
            username TEXT NOT NULL, ip TEXT NOT NULL, attempted_at REAL NOT NULL);
          CREATE INDEX IF NOT EXISTS registration_attempts_time ON registration_attempts(attempted_at);
          CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, username TEXT NOT NULL,
            action TEXT NOT NULL, detail TEXT NOT NULL, success INTEGER NOT NULL,
            app_session_id TEXT NOT NULL, ip TEXT NOT NULL);
        """)
        # One-time auth-mode migration. Preserve users/passwords/audit but revoke
        # old MFA-era sessions on first launch of username/password edition.
        db.execute("CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        # Legacy installations may already have accounts. Mark setup consumed so
        # it cannot be revived by deleting the last user row in-place.
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            db.execute("INSERT OR IGNORE INTO app_metadata(key,value) VALUES('initial_setup_completed','1')")
        current = db.execute("SELECT value FROM app_metadata WHERE key='authentication_mode'").fetchone()
        if current is None or current[0] != 'local_password_6327':
            db.execute("DELETE FROM login_sessions")
            db.execute("INSERT OR REPLACE INTO app_metadata(key,value) VALUES('authentication_mode','local_password_6327')")


def _public(row):
    if row is None:
        return None
    return {key: (bool(row[key]) if key == "is_active" else row[key])
            for key in ("id", "username", "display_name", "role", "is_active", "created_at")}


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def _validate_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 256:
        raise ValueError("Use a password with 10–256 characters.")


def users_exist():
    with _connect() as db:
        return db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


class InvalidSetupKey(ValueError):
    pass


class InitialSetupRateLimited(ValueError):
    pass


class InitialSetupAlreadyCompleted(ValueError):
    pass


def setup_key_is_secure(configured_key: str) -> bool:
    """Fail closed when no strong, server-side one-time setup key is configured."""
    return (isinstance(configured_key, str) and 32 <= len(configured_key) <= 256
            and re.fullmatch(r"[A-Za-z0-9_-]+", configured_key) is not None)


def initial_setup_open() -> bool:
    """Only the never-initialized database can run bootstrap setup."""
    with _connect() as db:
        return not (db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
                    or db.execute("SELECT 1 FROM app_metadata WHERE key='initial_setup_completed'").fetchone())


def create_initial_admin(username: str, password: str, display_name: str = "", *,
                         supplied_key: str, configured_key: str, ip: str = ""):
    """Provision first admin, atomically consuming bootstrap privilege in SQLite.

    A key is mandatory and checked with constant-time comparison. Invalid keys
    are rate-limited by client IP, including concurrent requests. Neither the
    key nor password is persisted to audit logs or application responses.
    """
    if not setup_key_is_secure(configured_key):
        raise InvalidSetupKey("Server setup key is not configured securely.")
    # Reject invalid keys before the expensive scrypt password hash.
    now = time.time()
    failure = False
    limited = False
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM initial_setup_attempts WHERE attempted_at < ?", (now - 900,))
        if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() or db.execute(
                "SELECT 1 FROM app_metadata WHERE key='initial_setup_completed'").fetchone():
            raise InitialSetupAlreadyCompleted("Initial administrator setup has already been completed.")
        attempts = db.execute("SELECT COUNT(*) FROM initial_setup_attempts WHERE ip=?", (ip,)).fetchone()[0]
        if attempts >= 8:
            limited = True
        elif not isinstance(supplied_key, str) or not hmac.compare_digest(supplied_key, configured_key):
            db.execute("INSERT INTO initial_setup_attempts VALUES(?,?)", (ip, now))
            failure = True
    if limited:
        raise InitialSetupRateLimited("Too many setup key attempts. Wait 15 minutes or contact the server administrator.")
    if failure:
        raise InvalidSetupKey("Invalid one-time setup key.")

    # Validate and hash only after verifying the secret. Never hash while holding
    # SQLite's write lock; an atomic second transaction prevents setup races.
    if not isinstance(username, str):
        raise ValueError("Enter a valid username.")
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,79}", username):
        raise ValueError("Username: 3–80 letters, numbers, dots, underscores, @ or hyphens.")
    _validate_password(password)
    name = _clean_display_name(display_name) if str(display_name or "").strip() else username
    hashed = _hash_password(password)
    try:
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() or db.execute(
                    "SELECT 1 FROM app_metadata WHERE key='initial_setup_completed'").fetchone():
                raise InitialSetupAlreadyCompleted("Initial administrator setup has already been completed.")
            cur = db.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) "
                             "VALUES(?,?,?,'admin',?)", (username, name, hashed, time.time()))
            db.execute("INSERT INTO app_metadata(key,value) VALUES('initial_setup_completed','1')")
            db.execute("DELETE FROM initial_setup_attempts")
            return _public(db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise InitialSetupAlreadyCompleted("Initial administrator setup has already been completed.") from exc


def create_user(username, password, display_name="", role="user", created_by=""):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,79}", username):
        raise ValueError("Username: 3–80 letters, numbers, dots, underscores, @ or hyphens.")
    _validate_password(password)
    if role not in {"admin", "user"}:
        raise ValueError("Invalid role.")
    password_hash = _hash_password(password)
    name = _clean_display_name(display_name) if str(display_name or "").strip() else username
    try:
        with _connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if created_by == "initial-setup":
                raise ValueError("Initial administrator must be created with the one-time setup key.")
            cur = db.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                             (username, name, password_hash, role, time.time()))
            # An administrator-created account supersedes any pending public request.
            db.execute("""UPDATE registration_requests SET status='rejected', password_hash='',
                reviewed_at=?, reviewed_by=? WHERE username=? AND status='pending'""",
                (time.time(), created_by or 'administrator', username))
            return _public(db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
    except sqlite3.IntegrityError as exc:
        raise ValueError("This username already exists.") from exc


def request_registration(username: str, display_name: str, password: str, ip: str) -> bool:
    """Queue a standard-user application only. Never create a usable account here.

    Return False for usernames that are unavailable or already pending, without
    distinguishing those cases to anonymous callers. Attempt tracking limits spam.
    """
    if not isinstance(username, str):
        raise ValueError("Enter a valid username.")
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@-]{2,79}", username):
        raise ValueError("Username: 3–80 letters, numbers, dots, underscores, @ or hyphens.")
    name = _clean_display_name(display_name)
    _validate_password(password)
    now = time.time()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM registration_attempts WHERE attempted_at < ?", (now - 86400,))
        ip_count = db.execute("SELECT COUNT(*) FROM registration_attempts WHERE ip=? AND attempted_at>?",
                              (ip, now - 3600)).fetchone()[0]
        name_count = db.execute("SELECT COUNT(*) FROM registration_attempts WHERE username=?",
                                (username,)).fetchone()[0]
        if ip_count >= 100 or name_count >= 3:
            raise ValueError("Too many registration attempts. Try again later or contact an administrator.")
        db.execute("INSERT INTO registration_attempts VALUES(?,?,?)", (username, ip, now))

    password_hash = _hash_password(password)
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return False
        existing = db.execute("SELECT status,requested_at FROM registration_requests WHERE username=?",
                              (username,)).fetchone()
        if existing:
            # No unbounded resubmission of previously rejected requests.
            if existing['status'] != 'rejected' or existing['requested_at'] > now - 86400:
                return False
            db.execute("""UPDATE registration_requests SET display_name=?, password_hash=?,
                status='pending', requested_at=?, reviewed_at=NULL, reviewed_by=NULL WHERE username=?""",
                (name, password_hash, now, username))
        else:
            db.execute("""INSERT INTO registration_requests
                (username,display_name,password_hash,status,requested_at)
                VALUES(?,?,?,'pending',?)""", (username, name, password_hash, now))
        return True


def list_pending_registrations():
    with _connect() as db:
        return [dict(row) for row in db.execute("""SELECT id,username,display_name,requested_at
            FROM registration_requests WHERE status='pending' ORDER BY requested_at ASC""")]


def review_registration(request_id: int, approve: bool, administrator: str):
    """Atomically approve to active standard user or reject and erase its hash."""
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM registration_requests WHERE id=?", (request_id,)).fetchone()
        if not row or row['status'] != 'pending':
            raise ValueError("This request is no longer pending. Refresh the list.")
        if approve and db.execute("SELECT 1 FROM users WHERE username=?", (row['username'],)).fetchone():
            raise ValueError("This username is already registered. Reject this request instead.")
        result = None
        if approve:
            cur = db.execute("""INSERT INTO users
                (username,display_name,password_hash,role,is_active,created_at)
                VALUES(?,?,?,'user',1,?)""",
                (row['username'], row['display_name'], row['password_hash'], time.time()))
            result = _public(db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())
        db.execute("""UPDATE registration_requests SET status=?, password_hash='', reviewed_at=?,
            reviewed_by=? WHERE id=? AND status='pending'""",
            ('approved' if approve else 'rejected', time.time(), administrator, request_id))
        return {'username': row['username'], 'approved': approve, 'user': result}


def authenticate(username, password, ip=""):
    username = str(username).strip().lower()[:80]
    now = time.time()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 900,))
        count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE username=?", (username,)).fetchone()[0]
        ip_count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)).fetchone()[0]
        if count >= 10 or ip_count >= 40:
            return None
        # Reserve an attempt before releasing the transaction to limit concurrent attacks.
        cur = db.execute("INSERT INTO login_attempts VALUES(?,?,?)", (username, ip, now))
        attempt_id = cur.lastrowid
        row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    encoded = row["password_hash"] if row else "scrypt$" + "00" * 16 + "$" + "00" * 64
    valid = False
    if isinstance(password, str) and len(password) <= 256:
        try:
            valid = hmac.compare_digest(_hash_password(password, encoded.split("$")[1]), encoded)
        except (ValueError, IndexError):
            pass
    if valid and row and row["is_active"]:
        with _connect() as db:
            db.execute("DELETE FROM login_attempts WHERE rowid=?", (attempt_id,))
        return _public(row)
    return None


def create_login_session(user_id, ip, agent):
    token = secrets.token_urlsafe(40)
    expires = time.time() + AUTH_SESSION_HOURS * 3600
    with _connect() as db:
        if db.execute("SELECT 1 FROM users WHERE id=? AND is_active=1", (user_id,)).fetchone() is None:
            raise ValueError("Account is unavailable.")
        db.execute("DELETE FROM login_sessions WHERE expires_at < ?", (time.time(),))
        db.execute("INSERT INTO login_sessions VALUES(?,?,?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, expires, ip, agent[:250]))
    return token, expires


def get_user_by_token(token, touch=False):
    if not token:
        return None
    with _connect() as db:
        row = db.execute("""SELECT u.* FROM users u JOIN login_sessions s ON s.user_id=u.id
          WHERE s.token_hash=? AND s.expires_at>? AND u.is_active=1""",
                         (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return _public(row)


def revoke_login_session(token):
    if token:
        with _connect() as db:
            db.execute("DELETE FROM login_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def list_users():
    with _connect() as db:
        return [_public(row) for row in db.execute("SELECT * FROM users ORDER BY username")]



def _clean_display_name(value):
    if not isinstance(value, str):
        raise ValueError("Enter a valid display name.")
    normalized = " ".join(value.strip().split())
    if not 2 <= len(normalized) <= 120 or any(ord(c) < 32 or ord(c) == 127 for c in normalized):
        raise ValueError("Display name must contain 2–120 visible characters.")
    return normalized


def update_display_name(user_id: int, display_name: str):
    """Account owner/admin may change display name, never the stable username."""
    name = _clean_display_name(display_name)
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Account was not found.")
        db.execute("UPDATE users SET display_name=? WHERE id=?", (name, user_id))
        return _public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def list_active_sessions(user_id: int, current_token: str):
    """Return only the signed-in owner's sessions; never expose stored token hashes."""
    current_hash = hashlib.sha256((current_token or "").encode()).hexdigest()
    with _connect() as db:
        rows = db.execute(
            "SELECT token_hash,expires_at,ip,agent FROM login_sessions "
            "WHERE user_id=? AND expires_at>? ORDER BY expires_at DESC",
            (user_id, time.time()),
        ).fetchall()
        return [{"current": row["token_hash"] == current_hash,
                 "expires_at": row["expires_at"], "ip": row["ip"],
                 "agent": row["agent"][:120]} for row in rows]


def revoke_other_sessions(user_id: int, current_token: str):
    """Keep the authenticated token and revoke only OTHER sessions of this owner."""
    if not current_token:
        raise ValueError("A current login session is required.")
    current_hash = hashlib.sha256(current_token.encode()).hexdigest()
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM login_sessions WHERE user_id=? AND token_hash=? AND expires_at>?",
                      (user_id, current_hash, time.time())).fetchone() is None:
            raise ValueError("Your current session has expired. Sign in again.")
        result = db.execute("DELETE FROM login_sessions WHERE user_id=? AND token_hash<>?", (user_id,current_hash))
        return result.rowcount


def set_user_role(user_id: int, role: str):
    """Administrative role update, protecting the final enabled administrator."""
    if role not in {'admin', 'user'}:
        raise ValueError("Role must be Administrator or Standard User.")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("Account was not found.")
        if row['role'] == 'admin' and row['is_active'] and role == 'user':
            remaining = db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0]
            if remaining <= 1:
                raise ValueError("Keep at least one active administrator.")
        db.execute("UPDATE users SET role=? WHERE id=?", (role,user_id))
        if row['role'] != role:
            db.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
        return _public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())

def set_user_active(user_id, is_active):
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User was not found.")
        if not is_active and row["role"] == "admin" and row["is_active"]:
            if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1").fetchone()[0] <= 1:
                raise ValueError("Keep at least one active administrator.")
        db.execute("UPDATE users SET is_active=? WHERE id=?", (int(is_active), user_id))
        if not is_active:
            db.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
        return _public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())


def reset_password(user_id, password):
    _validate_password(password)
    hashed = _hash_password(password)
    with _connect() as db:
        if not db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ValueError("User was not found.")
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (hashed, user_id))
        db.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
        return _public(db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())



def change_own_password(user_id: int, current_password: str, new_password: str):
    """Verify the current secret, then CAS-update and revoke every login token.

    This is separate from administrative resets: a signed-in account must know
    its own current password before changing it.
    """
    _validate_password(new_password)
    if not isinstance(current_password, str) or not current_password:
        raise ValueError("Enter your current password.")
    if current_password == new_password:
        raise ValueError("Choose a new password different from the current password.")
    with _connect() as db:
        row = db.execute("SELECT password_hash,is_active FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["is_active"]:
        raise ValueError("Account is not available.")
    current_hash = row["password_hash"]
    try:
        current_ok = hmac.compare_digest(_hash_password(current_password, current_hash.split("$")[1]), current_hash)
    except (ValueError, IndexError):
        current_ok = False
    if not current_ok:
        raise ValueError("Current password is incorrect.")
    new_hash = _hash_password(new_password)
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute(
            "UPDATE users SET password_hash=? WHERE id=? AND password_hash=? AND is_active=1",
            (new_hash, user_id, current_hash),
        )
        if changed.rowcount != 1:
            raise ValueError("Your password changed in another session. Please sign in again.")
        db.execute("DELETE FROM login_sessions WHERE user_id=?", (user_id,))
    return True


def client_ip(request):
    return request.client.host if request.client else ""


def user_agent(request):
    return request.headers.get("user-agent", "")[:250]


def record_audit(request, action, user=None, username="", detail="", success=True, app_session_id=""):
    with _connect() as db:
        db.execute("INSERT INTO audit(timestamp,username,action,detail,success,app_session_id,ip) VALUES(?,?,?,?,?,?,?)",
                   (time.time(), str((user or {}).get("username", username))[:80], action,
                    str(detail)[:2000], int(success), app_session_id, client_ip(request)))


def list_audit_logs(limit=250, username="", action="", status="all", session_id=""):
    query, params = "SELECT * FROM audit WHERE 1=1", []
    if username:
        query += " AND username=?"
        params.append(str(username).strip().lower()[:80])
    if action:
        query += " AND action=?"
        params.append(str(action)[:90])
    if status == "success":
        query += " AND success=1"
    elif status == "failure":
        query += " AND success=0"
    elif status != "all":
        raise ValueError("Invalid audit result filter.")
    if session_id:
        query += " AND app_session_id=?"
        params.append(str(session_id)[:80])
    with _connect() as db:
        return [dict(row) for row in db.execute(query + " ORDER BY id DESC LIMIT ?", params + [max(1, min(10000, int(limit)))])]


def audit_overview():
    """Administrative counts; 24h means trailing 24 hours, not calendar day."""
    since = time.time() - 86400
    with _connect() as db:
        counts = db.execute("""SELECT
          SUM(CASE WHEN action='LOGIN' AND success=1 AND timestamp>=? THEN 1 ELSE 0 END),
          SUM(CASE WHEN action='PROCESSING_STARTED' AND success=1 AND timestamp>=? THEN 1 ELSE 0 END),
          SUM(CASE WHEN success=0 AND timestamp>=? THEN 1 ELSE 0 END)
          FROM audit""", (since,since,since)).fetchone()
        actions = [row[0] for row in db.execute("SELECT DISTINCT action FROM audit ORDER BY action")]
        return {
            "backend": "Local SQLite (accounts and audit only)",
            "accounts": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "active_accounts": db.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0],
            "pending_registrations": db.execute("SELECT COUNT(*) FROM registration_requests WHERE status='pending'").fetchone()[0],
            "audit_events": db.execute("SELECT COUNT(*) FROM audit").fetchone()[0],
            "logins_24h": counts[0] or 0,
            "jobs_24h": counts[1] or 0,
            "failures_24h": counts[2] or 0,
            "actions": actions,
        }


def record_job_outcome_once(request, user, job, app_session_id):
    """On first terminal job observation, append a single audit entry per job."""
    if job.get("state") not in {"completed", "failed"}:
        return False
    action = "PROCESSING_COMPLETED" if job["state"] == "completed" else "PROCESSING_FAILED"
    job_id = str(job.get("job_id", ""))
    if not job_id:
        return False
    counts = (job.get("result") or {}).get("counts", {})
    detail = "job=" + job_id + "; status=" + str(job.get("state", "unknown")) + "; message excluded for privacy"
    if counts:
        detail += "; counts=" + ", ".join(f"{key}={value}" for key,value in counts.items())[:300]
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM audit WHERE app_session_id=? AND action=? AND detail LIKE ? LIMIT 1",
                      (app_session_id, action, "job=" + job_id + ";%" )).fetchone():
            return False
        db.execute("INSERT INTO audit(timestamp,username,action,detail,success,app_session_id,ip) VALUES(?,?,?,?,?,?,?)",
                   (time.time(), user["username"], action, detail[:2000], int(job["state"] == "completed"),
                    app_session_id, client_ip(request) if request is not None else "SYSTEM_WORKER"))
    return True


def database_stats():
    with _connect() as db:
        return {"backend": "Local SQLite", "database": DB_PATH.name,
                "accounts": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "audit_events": db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]}
