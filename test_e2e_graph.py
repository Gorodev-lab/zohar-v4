import httpx
import time
import sys

BASE_URL = "http://127.0.0.1:8004"

def run_test():
    print("=== INICIANDO PRUEBA E2E DE EXTRACCIÓN DE GRAFO ===")
    
    # 1. Obtener un documento MD de prueba real (ignorando índices)
    try:
        resp = httpx.get(f"{BASE_URL}/api/second_brain/notes")
        resp.raise_for_status()
        docs_data = resp.json()
        
        docs_list = []
        if isinstance(docs_data, list):
            docs_list = docs_data
        elif isinstance(docs_data, dict):
            for v in docs_data.values():
                if isinstance(v, list) and len(v) > 0:
                    docs_list = v
                    break
        
        # Filtramos '00_Index' u otros archivos que no sean de extracción real
        valid_docs = []
        for d in docs_list:
            name = d if isinstance(d, str) else d.get("name", str(d))
            if "Index" not in name:
                valid_docs.append(name)
                    
        if not valid_docs:
            print(f"❌ No se encontraron documentos válidos para extraer. Respuesta: {docs_data}")
            return
            
        doc_name = valid_docs[0]
        print(f"📄 Documento real seleccionado: {doc_name}")
        
    except Exception as e:
        print(f"❌ Error al contactar la API (obtener documentos): {repr(e)}")
        return

    # 2. Iniciar el bucle RSI
    print("\n🚀 Disparando orquestador RSI...")
    payload = {"doc_id": doc_name, "task": "Extraer entidades principales y relaciones para el grafo"}
    try:
        res = httpx.post(f"{BASE_URL}/api/rsi/run", json=payload).json()
        job_id = res.get("job_id")
        
        if not job_id:
            print(f"❌ La API no devolvió un job_id. Respuesta: {res}")
            return
            
        print(f"✅ Trabajo iniciado. Job ID: {job_id}")
    except Exception as e:
        print(f"❌ Error al iniciar RSI: {repr(e)}")
        return

    # 3. Polling del estado
    print("\n⏳ Monitoreando sub-agentes (Polling)...")
    while True:
        try:
            status_res = httpx.get(f"{BASE_URL}/api/rsi/status/{job_id}").json()
            status = status_res.get("status", "UNKNOWN")
            
            history = status_res.get("history", [])
            if history:
                last_action = history[-1].get("action_selected", "N/A")
                print(f"   -> Estado: {status} | Acción LLM: {last_action}")
            else:
                print(f"   -> Estado: {status} | Inicializando...")
                
            if status in ["COMPLETED", "FAILED", "PERSISTED", "MAX_ITERATIONS_REACHED"]:
                if status in ["FAILED", "MAX_ITERATIONS_REACHED"]:
                    print(f"⚠️ El trabajo finalizó con estado: {status} - {status_res.get('error', 'Sin error explícito')}")
                break

        except Exception as e:
            print(f"❌ Error de red durante el polling: {repr(e)}")
            break
            
        time.sleep(2.5)

    # 4. Validar que la base de datos se pobló correctamente
    print("\n📊 Consultando endpoint de Grafo (D3.js)...")
    try:
        graph_data = httpx.get(f"{BASE_URL}/api/graph/data").json()
        nodes = len(graph_data.get("nodes", []))
        links = len(graph_data.get("links", []))
        print(f"✅ EXTRACCIÓN FINALIZADA. La DB contiene: {nodes} Nodos y {links} Aristas.")
        
        if nodes > 0:
            print("\n🔍 Muestra de nodos extraídos:")
            for n in graph_data["nodes"][:5]:
                print(f"   - [{n.get('type')}] {n.get('id')} ({n.get('label')})")
    except Exception as e:
        print(f"❌ Error al obtener el grafo: {repr(e)}")

if __name__ == "__main__":
    run_test()
