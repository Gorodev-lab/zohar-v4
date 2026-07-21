import sqlite3
import time
from pathlib import Path

class ScraperLedger:
    def __init__(self, db_path: str = "scraper_ledger.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS descargas (
                    clave TEXT PRIMARY KEY,
                    status TEXT,
                    attempts INTEGER DEFAULT 0,
                    last_try REAL,
                    archivos TEXT
                )
            """)
            conn.commit()

    def get_status(self, clave: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM descargas WHERE clave = ?", (clave,)).fetchone()
            return dict(row) if row else None

    def mark_attempt(self, clave: str, status: str, archivos: str = ""):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO descargas (clave, status, attempts, last_try, archivos)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(clave) DO UPDATE SET
                    status = excluded.status,
                    attempts = attempts + 1,
                    last_try = excluded.last_try,
                    archivos = excluded.archivos
            """, (clave, status, time.time(), archivos))
            conn.commit()

    def is_done(self, clave: str) -> bool:
        record = self.get_status(clave)
        return record is not None and record["status"] == "done"
