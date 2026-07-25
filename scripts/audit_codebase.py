import os
import re
import ast
import glob

print("=== 🕵️‍♂️ INICIANDO AUDITORÍA ARQUITECTÓNICA DE ZOHAR V4 ===\n")

# 1. Mapeo de Rutas y Endpoints
api_files = glob.glob("**/*api*.py", recursive=True) + glob.glob("**/main.py", recursive=True) + glob.glob("**/app.py", recursive=True)
api_files = [f for f in api_files if ".venv" not in f and "node_modules" not in f]

print(f"📁 Archivos Principales de API/Servicios Detectados ({len(api_files)}):")
for f in api_files:
    print(f"   - {f}")

# 2. Búsqueda de Workers, Loops de Self-Healing, y Servicios Locales Rotos
keywords = {
    "Self-Healing / Reboots": r"(?i)(self_healing|restart_container|auto_repair)",
    "LLM Local (Llama:8083 / Ollama)": r"(?i)(localhost:8083|127\.0\.0\.1:8083|llama_server)",
    "RSI / Self-Research Loops": r"(?i)(rsi_loop|self_research|automejora|auto_improve)",
    "Generación Dinámica de Grafo": r"(?i)(def get_graph|build_graph|/api/graph)"
}

findings = {k: [] for k in keywords}

for root, _, files in os.walk("."):
    if any(x in root for x in [".git", ".venv", "node_modules", "extractions", "data/packages"]):
        continue
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                    for key, pattern in keywords.items():
                        matches = re.findall(pattern, content)
                        if matches:
                            findings[key].append((path, len(matches)))
            except Exception:
                pass

print("\n🔍 RESULTADOS DE INSPECION DE BUCLES Y DEPENDENCIAS:")
for category, matches in findings.items():
    print(f"\n📌 {category}:")
    if not matches:
        print("   (No se encontraron referencias directas)")
    else:
        for file_path, count in matches:
            print(f"   - {file_path} ({count} coincidencias)")

