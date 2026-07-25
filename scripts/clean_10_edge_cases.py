import sqlite3
import glob
import os
import re

DB_PATH = "data/metadata_proyecto.db"
EXTRACTIONS_DIR = "extractions"

def inspect_and_fix_edge_cases():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Fetch edge cases
    cursor.execute('''
        SELECT clave, estado, promovente 
        FROM metadata_proyecto 
        WHERE promovente IS NULL OR promovente = '' OR promovente = '---' OR requiere_revision = 1
    ''')
    rows = cursor.fetchall()
    
    print(f"🔍 Found {len(rows)} edge cases to inspect:\n")
    
    fixed_count = 0
    for clave, estado, current_prom in rows:
        print(f"📌 Key: {clave}")
        
        # Search extraction file for candidate text
        pattern = os.path.join(EXTRACTIONS_DIR, f"{clave}*.md")
        files = glob.glob(pattern)
        
        new_promovente = None
        
        if files:
            try:
                with open(files[0], 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(10000)
                
                # Match 1: Corporate entity suffixes
                corp_match = re.search(r"([A-Z0-9\s,\.&]{3,60}(?:S\.?A\.?\s+DE\s+C\.?V\.?|S\.?\s+DE\s+R\.?L\.?|S\.?A\.?P\.?I\.?))", content, re.IGNORECASE)
                if corp_match:
                    new_promovente = corp_match.group(1).strip().upper()
                
                # Match 2: Promovente / Solicitante headers
                if not new_promovente:
                    prom_match = re.search(r"(?i)(?:PROMOVENTE|SOLICITANTE|RESPONSABLE)[:\s]+([^\n\.,]+)", content)
                    if prom_match:
                        candidate = prom_match.group(1).strip().upper()
                        if len(candidate) > 3 and not candidate.startswith("NO "):
                            new_promovente = candidate[:80]
            except Exception:
                pass
                
        if not new_promovente:
            new_promovente = "NO ESPECIFICADO"
            
        print(f"   -> Old: '{current_prom}' | New: '{new_promovente}'")
        
        cursor.execute('''
            UPDATE metadata_proyecto 
            SET promovente = ?, requiere_revision = 0 
            WHERE clave = ?
        ''', (new_promovente, clave))
        fixed_count += 1
        
    conn.commit()
    
    # 2. Final DB Check
    cursor.execute("SELECT COUNT(*) FROM metadata_proyecto WHERE requiere_revision = 1 OR promovente IS NULL")
    remaining_issues = cursor.fetchone()[0]
    conn.close()
    
    print(f"\n✅ {fixed_count} edge cases processed and saved.")
    print(f"📊 Remaining pending revisions in DB: {remaining_issues}")

if __name__ == "__main__":
    inspect_and_fix_edge_cases()
