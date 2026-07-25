import sqlite3
import glob
import os
import re

DB_PATH = "data/metadata_proyecto.db"
EXTRACTIONS_DIR = "extractions"

ESTADOS_CANONICOS = [
    "AGUASCALIENTES", "BAJA CALIFORNIA", "BAJA CALIFORNIA SUR", "CAMPECHE", "CHIAPAS",
    "CHIHUAHUA", "COAHUILA", "COLIMA", "DISTRITO FEDERAL", "CIUDAD DE MEXICO", "DURANGO",
    "GUANAJUATO", "GUERRERO", "HIDALGO", "JALISCO", "MEXICO", "MICHOACAN", "MORELOS",
    "NAYARIT", "NUEVO LEON", "OAXACA", "PUEBLA", "QUERETARO", "QUINTANA ROO",
    "SAN LUIS POTOSI", "SINALOA", "SONORA", "TABASCO", "TAMAULIPAS", "TLAXCALA",
    "VERACRUZ", "YUCATAN", "ZACATECAS"
]

def clean_state_from_text(text):
    text_upper = text.upper()
    for est in ESTADOS_CANONICOS:
        if est in text_upper:
            return est.title()
    return None

def link_orphans():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Obtener claves huérfanas
    cursor.execute('''
        SELECT DISTINCT clave FROM documentos_proyecto 
        WHERE clave NOT IN (SELECT clave FROM metadata_proyecto) AND clave != 'SIN_CLAVE'
    ''')
    orphan_keys = [row[0] for row in cursor.fetchall()]
    print(f"Iniciando vinculación para {len(orphan_keys)} claves huérfanas...")
    
    linked_count = 0
    for clave in orphan_keys:
        # Buscar si existe extracción en markdown
        pattern = os.path.join(EXTRACTIONS_DIR, f"{clave}*.md")
        files = glob.glob(pattern)
        
        estado = None
        promovente = None
        
        if files:
            try:
                with open(files[0], 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(5000)
                estado = clean_state_from_text(content)
                
                # Intentar captura rápida de promovente
                prom_match = re.search(r"(?i)(?:PROMOVENTE|SOLICITANTE)[:\s]+([^\n]+)", content)
                if prom_match:
                    promovente = prom_match.group(1).strip().upper()[:100]
            except Exception:
                pass
                
        # Insertar ficha de proyecto vinculada
        cursor.execute('''
            INSERT OR IGNORE INTO metadata_proyecto (clave, estado, promovente, requiere_revision, snippet_fuente, fecha_extraccion, version_prompt)
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        ''', (clave, estado, promovente, 0 if promovente else 1, f"Orphan linked: {clave}", "v1.0-orphan-link"))
        linked_count += 1
        
    conn.commit()
    
    # Verificar cuántos huérfanos quedan
    cursor.execute('''
        SELECT COUNT(DISTINCT clave) FROM documentos_proyecto 
        WHERE clave NOT IN (SELECT clave FROM metadata_proyecto) AND clave != 'SIN_CLAVE'
    ''')
    remaining = cursor.fetchone()[0]
    conn.close()
    
    print(f"✅ {linked_count} proyectos vinculados e insertados en metadata_proyecto.")
    print(f"📊 Claves huérfanas restantes: {remaining}")

if __name__ == "__main__":
    link_orphans()
