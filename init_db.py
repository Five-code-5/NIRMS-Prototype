"""Run once to create a fresh database and the first admin account:
       python init_db.py
To ERASE everything and start over:  python init_db.py --force"""
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

import core

if os.path.exists(core.DB_PATH) and "--force" not in sys.argv:
    sys.exit(f"{core.DB_PATH} already exists. Run 'python init_db.py --force' to ERASE it and start over.")

db = sqlite3.connect(core.DB_PATH)
with open(os.path.join(core.BASE_DIR, "schema.sql"), encoding="utf-8") as f:
    db.executescript(f.read())
db.execute(
    "INSERT INTO users (full_name, email, office, password_hash, role) VALUES (?, ?, ?, ?, 'admin')",
    ("NOC Administrator", "admin@nirms.local", "DICT Region X - NOC", generate_password_hash("admin123")),
)
db.commit()
db.close()
print("Database ready.")
print("Admin login: admin@nirms.local / admin123  (you will be asked to change it at first login)")