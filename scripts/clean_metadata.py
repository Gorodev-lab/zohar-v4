import sqlite3
import re

DB_PATH = "data/metadata_proyecto.db"

ESTADOS_CANONICOS = [
    "AGUASCALIENTES", "BAJA CALIFORNIA", "BAJA CALIFORNIA SUR", "CAMPECHE", "CHIAPAS",
    "CHIHUAHUA", "COAHUILA", "COLIMA", "DISTRITO FEDERAL", "CIUDAD DE MEXICO", "DURANGO",
    "GUANAJUATO", "GUERRERO", "HIDALGO", "JALISCO", "MEXICO", "MICHOACAN", "MORELOS",
    "NAYARIT", "NUEVO LEON", "OAXACA", "PUEBLA", "QUERETARO", "QUINTANA ROO",
    "SAN LUIS POTOSI", "SINALOA", "SONORA", "TABASCO", "TAMAULIPAS", "TLAXCALA",
    "VERACRUZ", "YUCATAN", "ZACATECAS"
]

def clean_state(raw_state):
    if not raw_state or raw_state == "None":
        return None
    text_upper = raw_state.upper()
    for est in ESTADOS_CANONICOS:
        if est in text_upper:
            return est.title()
    return None

def sanitize_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT clave, estado, promovente FROM metadata_proyecto")
    rows = cursor.fetchall()
    
    cleaned_count = 0
    for clave, estado, promovente in rows:
        nuevo_estado = clean_state(str(estado))
        nuevo_promovente = None if promovente in ["None", "", None] else promovente
        
        cursor.execute("""
            UPDATE metadata_proyecto 
            SET estado = ?, promovente = ?
            WHERE clave = ?
        """, (nuevo_estado, nuevo_promovente, clave))
        cleaned_count += 1
        
    conn.commit()
    conn.close()
    print(f"✅ {cleaned_count} registros sanitizados correctamente.")

if __name__ == "__main__":
    sanitize_db()
