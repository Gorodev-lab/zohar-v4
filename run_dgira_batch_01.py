import asyncio
import re
import logging
import httpx
from pathlib import Path

# Importaciones precisas basadas en el grep
from scrapers.semarnat_downloader import SemarnatDownloader
from core.pdf_processor import iter_pages_as_markdown
from core.second_brain import SecondBrainBuilder

# --- INYECCIÓN: Auto-Sanador de Claves ---
def sanar_clave_dgira(clave_sucia):
    clave = clave_sucia.strip().upper()
    if len(clave) != 13: return None
    ocr_num_fixes = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2'}
    mascara = "NNLLNNNNLNNNN"
    clave_limpia = ""
    for i, char in enumerate(clave):
        if mascara[i] == 'N': clave_limpia += ocr_num_fixes.get(char, char)
        elif mascara[i] == 'L':
            if char == '0': clave_limpia += 'O'
            elif char == '1': clave_limpia += 'I'
            elif char == '5': clave_limpia += 'S'
            else: clave_limpia += char
    if re.match(r"^[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}$", clave_limpia):
        return clave_limpia
    return None
# ----------------------------------------


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Zohar-Batch-01")

API_URL = "http://127.0.0.1:8004"



async def get_pending_2026_keys() -> list[str]:
    claves_crudas = []
    try:
        with open("claves_pendientes.txt", "r", encoding="utf-8") as f:
            claves_crudas = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print("⚠️ No se encontró claves_pendientes.txt. Asegúrate de extraer las gacetas primero.")
        return []
        
    claves_limpias = []
    for c in claves_crudas:
        sanada = sanar_clave_dgira(c)
        if sanada:
            claves_limpias.append(sanada)
            
    return claves_limpias

async def process_batch():
    pending_keys = await get_pending_2026_keys()
    
    # Ruteo Inteligente DGIRA (letras E, H, T, U, V, I, M)
    dgira_keys = [k for k in pending_keys if re.search(r'2026[EHTUVIM]', k)]
    
    # Tomamos estrictamente el primer bloque de 10
    batch = dgira_keys
    logger.info(f"🚀 INICIANDO LOTE 1: {len(batch)} claves DGIRA seleccionadas.")
    
    downloader = SemarnatDownloader(download_dir="downloads")
    # Inicializamos el constructor del Second Brain (asumiendo raíz "." o ajusta a tu ruta de bóveda)
    brain_builder = SecondBrainBuilder(base_dir=Path(".")) 
    
    for idx, clave in enumerate(batch, 1):
        logger.info(f"\n" + "="*50)
        logger.info(f"--- Procesando [{idx}/10]: {clave} ---")
        try:
            # 1. Scraping y Descarga (Selector arreglado)
            logger.info(f"[{clave}] 1. Descargando desde portal SEMARNAT...")
            download_paths = downloader.descargar_clave(clave)
            
            if not download_paths:
                logger.warning(f"[{clave}] No hay documentos nuevos/válidos. Saltando.")
                continue
                
            # 2. Conversión PDF -> Markdown
            logger.info(f"[{clave}] 2. Convirtiendo PDFs a Markdown...")
            md_files = []
            for path in download_paths:
                path_obj = Path(path)
                if path_obj.suffix.lower() != '.pdf':
                    continue
                    
                md_path = path_obj.with_suffix('.md')
                with open(md_path, 'w', encoding='utf-8') as f:
                    for page_text in iter_pages_as_markdown(str(path_obj)):
                        if page_text:
                            f.write(page_text + "\n\n")
                            
                md_files.append(md_path)
                logger.info(f"[{clave}] MD Generado: {md_path.name}")

            # 3. Sincronización Second Brain
            logger.info(f"[{clave}] 3. Actualizando índices del Second Brain...")
            brain_builder.build_vault() # Reconstruye índices locales para detectar los nuevos MDs
            
            # 4. Inferencia IA & Ingesta DW (Delegado a la API Local)
            logger.info(f"[{clave}] 4. Disparando Orquestador RSI (Inferencia y Grafo)...")
            for md_file in md_files:
                payload = {
                    "doc_id": md_file.name, 
                    "task": "Extraer entidades principales, promotores y relaciones para el grafo"
                }
                
                try:
                    res = httpx.post(f"{API_URL}/api/rsi/run", json=payload, timeout=15.0)
                    if res.status_code == 200:
                        job_id = res.json().get("job_id")
                        logger.info(f"[{clave}] -> Job RSI iniciado. Job ID: {job_id}")
                        
                        # Polling para proteger a Gemma (espera que acabe un doc antes de mandar el otro)
                        logger.info(f"[{clave}] -> Esperando inferencia de llama-server...")
                        while True:
                            status_res = httpx.get(f"{API_URL}/api/rsi/status/{job_id}").json()
                            status = status_res.get("status", "UNKNOWN")
                            if status in ["COMPLETED", "FAILED", "PERSISTED"]:
                                logger.info(f"[{clave}] -> Resultado RSI: {status}")
                                break
                            await asyncio.sleep(4.0) # Ciclo de espera suave
                            
                    else:
                        logger.error(f"[{clave}] Error HTTP al iniciar RSI: {res.status_code}")
                except Exception as e_api:
                    logger.error(f"[{clave}] Fallo de conexión con la API FastAPI: {e_api}")
                    
            logger.info(f"✅ [{clave}] Flujo completo (Descarga -> MD -> Inferencia -> DW).")
            
        except Exception as e:
            logger.error(f"❌ Error general procesando {clave}: {str(e)}")
            continue

    logger.info("\n" + "="*50)
    logger.info("⏸️ LOTE 1 FINALIZADO Y ALMACENADO. Esperando confirmación manual para bloque 2.")

if __name__ == "__main__":
    asyncio.run(process_batch())
