import sqlite3
import re
import glob
import os

DB_PATH = "data/metadata_proyecto.db"
EXTRACTIONS_DIR = "extractions"

def extract_from_text(text):
    promovente, municipio = None, None
    
    # Buscar Promovente (heurística)
    prom_match = re.search(r"(?i)(?:promovente|raz[oó]n social|empresa|por parte de)[\s\:\*]+([A-Z0-9ÁÉÍÓÚÑ\s\,\.\-\&]{5,100}?)(?:\n|\*|\r|$|,)", text)
    if prom_match:
        promovente = prom_match.group(1).strip().upper()
        
    # Buscar Municipio (heurística)
    mun_match = re.search(r"(?i)(?:municipio de|en el municipio|delegaci[oó]n)\s+([A-ZÁÉÍÓÚÑ\s]{3,50}?)(?:\,|en el estado|estado de|\n|\*|\.)", text)
    if mun_match:
        municipio = mun_match.group(1).strip().title()
        
    return promovente, municipio

def fill_missing():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Buscar los que tienen al menos un campo vacío
    cursor.execute("SELECT clave FROM metadata_proyecto WHERE promovente IS NULL OR municipio IS NULL")
    rows = cursor.fetchall()
    
    updated_count = 0
    print(f"Buscando datos faltantes para {len(rows)} proyectos...")
    
    for (clave,) in rows:
        pattern = os.path.join(EXTRACTIONS_DIR, f"{clave}*.md")
        files = glob.glob(pattern)
        
        if not files:
            continue
            
        try:
            with open(files[0], 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()[:3000] # Solo necesitamos leer el inicio del documento
                
            promovente, municipio = extract_from_text(text)
            
            if promovente or municipio:
                cursor.execute("""
                    UPDATE metadata_proyecto 
                    SET promovente = COALESCE(?, promovente),
                        municipio = COALESCE(?, municipio),
                        requiere_revision = 0
                    WHERE clave = ?
                """, (promovente, municipio, clave))
                updated_count += 1
                
        except Exception as e:
            pass
            
    conn.commit()
    
    # Verificar cuántos siguen vacíos
    cursor.execute("SELECT COUNT(*) FROM metadata_proyecto WHERE promovente IS NULL")
    quedan_vacios = cursor.fetchone()[0]
    conn.close()
    
    print(f"✅ {updated_count} proyectos actualizados con éxito.")
    print(f"⚠️ Aún quedan {quedan_vacios} proyectos sin Promovente detectado.")

if __name__ == "__main__":
    fill_missing()
