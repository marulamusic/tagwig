import sqlite3
from pathlib import Path


class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT,
                library_path TEXT,
                name TEXT,
                category TEXT,
                tags TEXT,
                bpm INTEGER,
                key TEXT,
                label TEXT,
                date_added TEXT
            );
        """)
        # Migrate existing databases that predate the label column
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(samples)").fetchall()]
        if "label" not in cols:
            self.conn.execute("ALTER TABLE samples ADD COLUMN label TEXT DEFAULT ''")
            self.conn.commit()
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

    def save_sample(self, data):
        self.conn.execute("""
            INSERT INTO samples
            (original_path, library_path, name, category, tags, bpm, key, label, date_added)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("original_path", ""),
            data.get("library_path", ""),
            data.get("name", ""),
            data.get("category", ""),
            data.get("tags", ""),
            data.get("bpm", 0),
            data.get("key", ""),
            data.get("label", ""),
            data.get("date_added", ""),
        ))
        self.conn.commit()

    def get_all_samples(self) -> list[dict]:
        """Return every sample record as a list of dicts."""
        rows = self.conn.execute("SELECT * FROM samples").fetchall()
        return [dict(r) for r in rows]

    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def save_setting(self, key, value):
        self.conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
