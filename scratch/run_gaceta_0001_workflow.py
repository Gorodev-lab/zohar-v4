#!/usr/bin/env python3
"""
scratch/run_gaceta_0001_workflow.py
Ejecuta el pipeline completo de manera simulada y acelerada para la primera gaceta
del corpus (gaceta_0001-26), permitiendo visualizar las claves extraídas,
sus estados y la wiki asociada sin retrasos por Selenium.
"""

import sys
import re
import csv
import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads"
GACETAS_DIR = DOWNLOADS_DIR / "gacetas"
EXTRACTIONS_DIR = BASE_DIR / "extractions"
INFERENCE_CACHE_DIR = DATA_DIR / "inference_cache"

# Claves SINAT regex
CLAVE_RE = re.compile(r"\b(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})\b")

def run():
    print("Iniciando pipeline acelerado para la primera gaceta...")
    
    # 1. Asegurar directorios
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INFERENCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (DOWNLOADS_DIR / "estudios").mkdir(parents=True, exist_ok=True)
    (DOWNLOADS_DIR / "resolutivos").mkdir(parents=True, exist_ok=True)
    (DOWNLOADS_DIR / "resumenes").mkdir(parents=True, exist_ok=True)

    gaceta_md_path = EXTRACTIONS_DIR / "gaceta_0001-26.md"
    if not gaceta_md_path.exists():
        print(f"Error: No existe la extracción Markdown de la gaceta en: {gaceta_md_path}")
        sys.exit(1)

    # 2. Leer texto y extraer claves
    print(f"Leyendo contenido de {gaceta_md_path.name}...")
    content = gaceta_md_path.read_text(encoding="utf-8", errors="ignore")
    found_keys = list(set(CLAVE_RE.findall(content.upper())))
    print(f"Encontradas {len(found_keys)} claves SINAT en la gaceta: {found_keys}")

    # 3. Escribir al CSV claves_2026.csv
    csv_path = DATA_DIR / "claves_2026.csv"
    gaceta_pdf_path = GACETAS_DIR / "gaceta_0001-26.pdf"

    # Cargar claves existentes para no borrarlas
    existing_rows = []
    if csv_path.exists():
        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                existing_rows = list(reader)
        except Exception:
            pass

    # Filtrar las filas viejas de gaceta_0001-26
    new_rows = [r for r in existing_rows if "gaceta_0001-26" not in Path(r.get("FILE", "")).name]
    
    # Agregar las nuevas claves extraídas
    for clave in found_keys:
        new_rows.append({
            "CLAVE": clave,
            "YEAR": 2026,
            "FILE": str(gaceta_pdf_path)
        })

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["CLAVE", "YEAR", "FILE"])
        writer.writeheader()
        writer.writerows(new_rows)
    print(f"CSV de claves actualizado con {len(found_keys)} proyectos para gaceta_0001-26.")

    # 4. Simular descargas, conversiones y dictámenes para los primeros proyectos
    # Para demostración rápida, procesaremos las primeras 3 claves reales de la gaceta
    proyectos_a_procesar = found_keys[:3]
    print(f"Procesando en detalle los proyectos: {proyectos_a_procesar}...")

    for clave in proyectos_a_procesar:
        # Simular descarga del PDF del estudio
        estudio_pdf = DOWNLOADS_DIR / "estudios" / f"{clave}.pdf"
        if not estudio_pdf.exists():
            estudio_pdf.write_text(f"PDF Ficticio de Estudio para clave {clave}", encoding="utf-8")
        
        # Simular resolutivo PDF para algunas claves
        if clave == proyectos_a_procesar[0]:
            resolutivo_pdf = DOWNLOADS_DIR / "resolutivos" / f"{clave}.pdf"
            if not resolutivo_pdf.exists():
                resolutivo_pdf.write_text(f"PDF Ficticio de Resolutivo para clave {clave}", encoding="utf-8")

        # Simular conversión Markdown (.md)
        extraction_md = EXTRACTIONS_DIR / f"{clave}.md"
        if not extraction_md.exists():
            extraction_md.write_text(
                f"# Estudio de Impacto Ambiental: {clave}\n\n"
                f"Este es el contenido de texto extraído del estudio ambiental {clave}.\n"
                f"El proyecto se ubica cerca de un área con vegetación protegida.\n"
                f"Se detectaron posibles impactos a la fauna silvestre local.\n",
                encoding="utf-8"
            )

        # Simular dictamen de Inferencia IA
        inference_json = INFERENCE_CACHE_DIR / f"{clave}.json"
        if not inference_json.exists():
            dictamen_data = {
                "veredicto": "CONDICIONADO" if clave == proyectos_a_procesar[0] else "VIABLE",
                "score": 0.75 if clave == proyectos_a_procesar[0] else 0.90,
                "confianza_pct": 85,
                "yes_signals": [
                    "Cuenta con plan de reforestación",
                    "Uso de energías limpias en sitio"
                ],
                "no_signals": [
                    "Afectación temporal a cauce de agua secundario"
                ],
                "knockouts": [],
                "condicionantes": [
                    "Implementar barreras de desviación de fauna",
                    "Monitoreo biológico semestral"
                ] if clave == proyectos_a_procesar[0] else [],
                "meta": {
                    "modelo": "gemini-1.5-flash-local-mock"
                }
            }
            inference_json.write_text(json.dumps(dictamen_data, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. Compilar base de datos del Second Brain
    print("Sincronizando la Base de Datos de Conocimiento...")
    sys.path.append(str(BASE_DIR))
    from core.second_brain import SecondBrainBuilder
    builder = SecondBrainBuilder(BASE_DIR)
    stats = builder.build_vault()
    print(f"Sincronización terminada con éxito: {stats}")
    print("Workflow completado exitosamente.")

if __name__ == "__main__":
    run()
