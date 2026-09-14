#!/usr/bin/env python3
"""Print a bcrypt hash for TP_ADMIN_PASSWORD_HASH. Usage:
    python3 gen_admin_hash.py [password]
If no password is given as an argument, it is read from stdin/prompt.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import hash_password  # noqa: E402

if __name__ == "__main__":
    pw = sys.argv[1] if len(sys.argv) > 1 else input("Password: ")
    print(hash_password(pw))
