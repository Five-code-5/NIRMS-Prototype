
import sqlite3

import core

NEW_COLUMNS = [
    ("device_type",  "TEXT NOT NULL DEFAULT ''"),
    ("serial_no",    "TEXT NOT NULL DEFAULT ''"),
    ("due_date",     "TEXT NOT NULL DEFAULT ''"),
    ("repair_type",  "TEXT NOT NULL DEFAULT ''"),
    ("action_taken", "TEXT NOT NULL DEFAULT ''"),
    ("pre_test",     "TEXT NOT NULL DEFAULT 'Not done'"),
    ("post_test",    "TEXT NOT NULL DEFAULT 'Not done'"),
    ("resolved_at",  "TEXT NOT NULL DEFAULT ''"),
]

db = sqlite3.connect(core.DB_PATH)
have = {row[1] for row in db.execute("PRAGMA table_info(reports)")}
added = []
for name, ddl in NEW_COLUMNS:
    if name not in have:
        db.execute(f"ALTER TABLE reports ADD COLUMN {name} {ddl}")
        added.append(name)

# Give older reports a due date based on their priority
for priority, days in core.DUE_DAYS.items():
    db.execute("UPDATE reports SET due_date = date(created_at, ?) WHERE due_date = '' AND priority = ?",
               (f"+{days} days", priority))
db.commit()
db.close()
print("Added columns:", ", ".join(added) if added else "none (already up to date)")