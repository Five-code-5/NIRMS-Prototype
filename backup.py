"""Make a safe copy of the database:   python backup.py
Copies are saved in the 'backups' folder with the date and time in the name."""
import os
import sqlite3
from datetime import datetime

import core

folder = os.path.join(core.BASE_DIR, "backups")
os.makedirs(folder, exist_ok=True)
target = os.path.join(folder, datetime.now().strftime("nirms-%Y%m%d-%H%M%S.db"))

source = sqlite3.connect(core.DB_PATH)
copy = sqlite3.connect(target)
source.backup(copy)          # safe even while the apps are running
copy.close()
source.close()
print("Backup saved:", target)