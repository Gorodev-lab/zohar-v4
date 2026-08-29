import os
import glob
import json
import sqlite3
from datetime import datetime

DB_PATH = "data/metadata_proyecto.db"
EXTRACTIONS_DIR = "extractions"

def get_processed_keys():
    if not os.path.exists(DB_PATH):
        return set()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT clave FROM metadata_proyecto")
        keys = {row[0] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        keys = set()
    conn.close()
    return keys

def list_pending_files():
    processed = get_processed_keys()
    pattern = os.path.join(EXTRACTIONS_DIR, "*.md")
    files = glob.glob(pattern)
    pending = []
    for f in sorted(files):
        filename = os.path.basename(f)
        clave = filename.split(".")[0]
        if clave not in processed:
            pending.append((clave, f))
    return pending

if __name__ == "__main__":
    pending = list_pending_files()
    print(f"Archivos pendientes por procesar: {len(pending)}")
    for clave, path in pending:
        print(f" - Clave: {clave} | Ruta: {path}")

    # CS329A idea 3: eval suite después del batch — cada corrida deja línea
    # en la serie histórica (regresiones auditable al cambiar prompt/modelo)
    if pending:
        try:
            import subprocess as sp
            r = sp.run(["python3", str(Path(__file__).parent.parent / "eval_suite.py")],
                       capture_output=True, text=True, timeout=120)
            print(r.stdout[-500:])
        except Exception as e:
            print(f"[eval_suite] no corrió: {e}")
