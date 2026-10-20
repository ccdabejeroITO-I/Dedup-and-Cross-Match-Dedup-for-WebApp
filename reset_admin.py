"""Local-only recovery for an existing CrossMatch v6.31 account database.

Run on the computer that holds the deployed database (or in its Docker web
container). This program does not provide an unauthenticated HTTP reset API.
"""
from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
import time
from pathlib import Path

from services import auth_manager as auth


def reset_existing_admin(db_path: Path, requested_user: str = "") -> tuple[str, str]:
    """Change ONE existing admin's username/password, retaining every other table.

    A missing database must not silently result in a second, empty installation.
    """
    if not db_path.is_file():
        raise ValueError(
            f"No existing account database found: {db_path}\n"
            "Copy RESET_ADMIN.bat and reset_admin.py into your ORIGINAL installed "
            "CrossMatch folder (beside its database folder), then run again. "
            "For a new installation, create an administrator via /setup instead."
        )
    with sqlite3.connect(db_path, timeout=30) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        admins = db.execute(
            "SELECT id, username, is_active FROM users WHERE role='admin' ORDER BY id"
        ).fetchall()
        if not admins:
            raise ValueError("This database has no administrator. No accounts were changed.")

        selected = None
        if requested_user:
            selected = next((r for r in admins if r["username"] == requested_user.strip().lower()), None)
            if selected is None:
                raise ValueError("That existing username is not an administrator. No accounts were changed.")
        elif len(admins) == 1:
            selected = admins[0]
        else:
            selected = next((r for r in admins if r["username"] == "admin"), None)
            if selected is None:
                print("Several administrator accounts exist. Select ONE to reset:")
                for i, row in enumerate(admins, 1):
                    print(f"  {i}. {row['username']} ({'active' if row['is_active'] else 'inactive'})")
                choice = input("Administrator number (blank to cancel): ").strip()
                if not choice.isdigit() or not 1 <= int(choice) <= len(admins):
                    raise ValueError("Reset cancelled; no accounts were changed.")
                selected = admins[int(choice) - 1]

        # Standardize to 'admin' only if that username is unoccupied or already ours.
        conflicting = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        new_username = "admin" if conflicting is None or conflicting["id"] == selected["id"] else selected["username"]
        old_username = selected["username"]
        temporary_password = secrets.token_urlsafe(22)  # cryptographically random, printed only once
        hashed = auth._hash_password(temporary_password)
        # Commit all related changes together, including invalidating old login tokens.
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE users SET username=?,password_hash=?,is_active=1 WHERE id=? AND role='admin'",
            (new_username, hashed, selected["id"]),
        )
        db.execute("DELETE FROM login_sessions WHERE user_id=?", (selected["id"],))
        db.execute("DELETE FROM login_attempts WHERE username IN (?,?)", (old_username, new_username))
        db.execute(
            "INSERT INTO audit(timestamp,username,action,detail,success,app_session_id,ip) "
            "VALUES(?,?,?,?,?,?,?)",
            (time.time(), new_username, "LOCAL_ADMIN_RESET", "On-machine recovery; prior sessions revoked", 1, "", "LOCAL_CONSOLE"),
        )
        db.commit()
        return new_username, temporary_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset an admin of an existing installed CrossMatch account database.")
    parser.add_argument("--user", default="", help="Existing administrator username, if multiple admins exist")
    args = parser.parse_args(argv)
    try:
        username, password = reset_existing_admin(auth.DB_PATH, args.user)
    except (ValueError, sqlite3.Error, EOFError, KeyboardInterrupt) as exc:
        print(f"\nReset NOT performed: {exc}", file=sys.stderr)
        return 1
    print("\nADMIN ACCOUNT RESET SUCCESSFUL")
    print(f"Username: {username}")
    print(f"Temporary password: {password}")
    print("\nStore this password securely. It is displayed here once and NOT written to a file.")
    print("Other accounts, audit history and matching results have been preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
