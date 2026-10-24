DROP TABLE IF EXISTS report_updates;
DROP TABLE IF EXISTS reports;
DROP TABLE IF EXISTS users;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    office        TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'client' CHECK (role IN ('client', 'admin')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no    TEXT NOT NULL UNIQUE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    category     TEXT NOT NULL,
    location     TEXT NOT NULL,
    description  TEXT NOT NULL,
    device_type  TEXT NOT NULL DEFAULT '',
    serial_no    TEXT NOT NULL DEFAULT '',
    priority     TEXT NOT NULL DEFAULT 'Medium'
                 CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
    status       TEXT NOT NULL DEFAULT 'Open'
                 CHECK (status IN ('Open', 'In Progress', 'Resolved', 'Closed')),
    due_date     TEXT NOT NULL DEFAULT '',
    assigned_to  TEXT NOT NULL DEFAULT '',
    repair_type  TEXT NOT NULL DEFAULT '',
    action_taken TEXT NOT NULL DEFAULT '',
    pre_test     TEXT NOT NULL DEFAULT 'Not done',
    post_test    TEXT NOT NULL DEFAULT 'Not done',
    remarks      TEXT NOT NULL DEFAULT '',
    resolved_at  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE report_updates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    changed_by  TEXT NOT NULL,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);