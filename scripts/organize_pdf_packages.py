import os
import re
import glob
import shutil
import sqlite3
from datetime import datetime

SOURCE_DIR = "downloads"
PACKAGES_DIR = "data/packages"
DB_PATH = "data/metadata_proyecto.db"

# Expresión regular para detectar claves tipo MIA (ej: 12GE2026V0006)
CLAVE_REGEX = re.compile(r'\b[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}\b')

def classify_doc_type(filename, text_sample=""):
    content = (filename + " " + text_sample).upper()
    if any(k in content for k in ["RESOLUCION", "RESOLUTIVO", "AUTORIZACION", "SGPA", "OFICIO"]):
        return "resolutivos"
    elif any(k in content for k in ["MIA", "ESTUDIO", "TECNICO", "IMPACTO", "MODALIDAD"]):
        return "estudios"
    elif any(k in content for k in ["RESUMEN", "GACETA", "SINET", "EXTRACTO"]):
        return "resumenes"
    return "otros"

def process_pdfs():
    os.makedirs(PACKAGES_DIR, exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    pdf_files = glob.glob(f"{SOURCE_DIR}/**/*.pdf", recursive=True)
    print(f"[{datetime.now().isoformat()}] Inicio de proceso AFK. Total de PDFs hallados: {len(pdf_files)}")
    
    processed_count = 0
    for pdf_path in pdf_files:
        filename = os.path.basename(pdf_path)
        
        # 1. Intentar extraer la clave del nombre del archivo o ruta
        match = CLAVE_REGEX.search(pdf_path)
        clave = match.group(0) if match else "SIN_CLAVE"
        
        # 2. Clasificar tipo de documento
        tipo = classify_doc_type(filename)
        
        # 3. Crear directorio del paquete
        dest_dir = os.path.join(PACKAGES_DIR, clave, tipo)
        os.makedirs(dest_dir, exist_ok=True)
        
        dest_path = os.path.join(dest_dir, filename)
        
        # Copiar archivo al paquete organizado
        shutil.copy2(pdf_path, dest_path)
        
        # 4. Registrar en SQLite
        try:
            cursor.execute('''
                INSERT INTO documentos_proyecto (clave, tipo_documento, nombre_archivo, ruta_origen, ruta_paquete, fecha_ingesta)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(clave, nombre_archivo) DO UPDATE SET
                    tipo_documento=excluded.tipo_documento,
                    ruta_paquete=excluded.ruta_paquete,
                    fecha_ingesta=excluded.fecha_ingesta
            ''', (clave, tipo, filename, pdf_path, dest_path, datetime.utcnow().isoformat()))
            conn.commit()
            processed_count += 1
        except Exception as e:
            print(f"Error registrando {filename}: {e}")

    conn.close()
    print(f"[{datetime.now().isoformat()}] Proceso completado. Archivos empaquetados y registrados: {processed_count}")

if __name__ == "__main__":
    process_pdfs()
