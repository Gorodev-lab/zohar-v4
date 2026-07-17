import os
import sys
import json
import subprocess
import argparse
import re
import httpx
from dotenv import load_dotenv

load_dotenv()

# Respaldos en disco para reversión
BACKUP_PATH = "infer.py.bak"

def get_current_score(python_exe: str) -> float:
    """
    Ejecuta eval_zohar.py y extrae el score final de la salida.
    Si ocurre un error, retorna 0.0.
    """
    try:
        res = subprocess.run(
            [python_exe, "eval_zohar.py"],
            capture_output=True,
            text=True,
            timeout=120.0
        )
        if res.returncode != 0:
            print(f"Warning: eval_zohar.py falló con código {res.returncode}", file=sys.stderr)
            print(f"Stderr: {res.stderr}", file=sys.stderr)
            return 0.0
            
        # Buscar "SCORE: <valor>" en stdout
        stdout_clean = res.stdout.strip()
        match = re.search(r"SCORE:\s*([0-9.]+)", stdout_clean)
        if match:
            return float(match.group(1))
        
        print(f"Warning: No se encontró el patrón de SCORE en la salida de eval_zohar.py.", file=sys.stderr)
        print(f"Stdout: {res.stdout}", file=sys.stderr)
        return 0.0
    except Exception as e:
        print(f"Error ejecutando eval_zohar.py: {e}", file=sys.stderr)
        return 0.0

import re

def call_meta_model(prompt: str) -> str:
    """
    Envía el prompt de optimización al llama-server local (Gemma 4 E2B).
    Usa el formato de chat de Gemma con los turnos start_of_turn/end_of_turn.
    """
    local_url = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8083").rstrip("/")
    model_name = os.getenv("LOCAL_LLM_MODEL", "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf")

    # Formatear con el template de chat Gemma
    formatted_prompt = (
        "<start_of_turn>user\n"
        f"{prompt.strip()}"
        "<end_of_turn>\n"
        "<start_of_turn>model\n"
    )

    print(f"Usando llama-server local ({model_name}) para el Meta-Optimizador...", file=sys.stderr)

    payload = {
        "prompt": formatted_prompt,
        "temperature": 0.5,
        "n_predict": 2048,
        "stop": ["<end_of_turn>", "<eos>"],
    }

    try:
        r = httpx.post(
            f"{local_url}/completion",
            json=payload,
            timeout=180.0,
        )
        if r.status_code == 200:
            return r.json().get("content", "").strip()
        else:
            print(f"Error HTTP {r.status_code} desde llama-server: {r.text}", file=sys.stderr)
    except Exception as exc:
        print(f"Error crítico llamando al llama-server: {exc}", file=sys.stderr)

    return ""

def clean_python_code(raw_response: str) -> str:
    """
    Limpia la respuesta del modelo para extraer únicamente el código Python.
    """
    cleaned = raw_response.strip()
    
    # Intentar extraer bloques triple comilla de python
    match = re.search(r"```python\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
        
    match_any = re.search(r"```\s*(.*?)\s*```", cleaned, re.DOTALL)
    if match_any:
        # Validar si parece código python
        content = match_any.group(1).strip()
        if "import " in content or "def " in content:
            return content

    # Si no tiene bloques de código, retornar el texto si contiene código legible
    if "import " in cleaned or "def " in cleaned:
        return cleaned
        
    return ""

def main():
    parser = argparse.ArgumentParser(description="Bucle de Mejora Continua Recursiva (RSI)")
    parser.add_argument("--iterations", type=int, default=5, help="Número de iteraciones del bucle de auto-investigación")
    args = parser.parse_args()

    python_exe = ".venv/bin/python" if os.path.exists(".venv/bin/python") else "python"
    
    print("=== INICIANDO BUCLE RSI PARA ZOHAR V4 ===")
    
    # 1. Calcular score base inicial
    print("Calculando Score inicial de infer.py...")
    initial_score = get_current_score(python_exe)
    print(f"Score inicial obtenido: {initial_score:.4f}")
    
    best_score = initial_score
    
    for i in range(1, args.iterations + 1):
        print(f"\n--- Iteración RSI {i} de {args.iterations} ---")
        
        # Leer el código actual de infer.py
        with open("infer.py", "r", encoding="utf-8") as f:
            current_code = f.read()
            
        # Crear backup del archivo actual
        with open(BACKUP_PATH, "w", encoding="utf-8") as f:
            f.write(current_code)
            
        # Construir prompt del meta-modelo
        meta_prompt = f"""
Eres un Ingeniero de IA Senior y tu objetivo es optimizar el código Python del script extractor "infer.py".
El script actual tiene un Score de Precisión de {best_score:.4f} (donde 1.0 es precisión perfecta).

Tu tarea:
Propón una modificación al código Python de infer.py para subir el Score. Puedes mejorar:
1. El prompt de extracción dentro de la función `extract_entities()`.
2. Las instrucciones de formato JSON requeridas al modelo.
3. El manejo y parseo seguro de la respuesta.
4. Ajustes en las opciones de Ollama (ej. temperature, system prompt, stop tokens).
5. Validaciones extras por reglas antes de emitir la salida.

RESTRICCIONES:
- Mantén la misma interfaz de consola (debe recibir `--clave`).
- Debe imprimir el JSON final a stdout.
- Mantén el uso de SQLAlchemy puro para persistencia en base de datos.
- Devuelve únicamente el código Python completo, correcto y ejecutable. No incluyas explicaciones.
- Escribe el código dentro de un bloque markdown ```python y ```.

Código de infer.py actual:
\"\"\"
{current_code}
\"\"\"

Genera el código de infer.py optimizado:
"""

        # Llamar al meta-modelo para proponer optimizaciones
        raw_proposal = call_meta_model(meta_prompt)
        proposed_code = clean_python_code(raw_proposal)
        
        if not proposed_code:
            print("Error: El meta-modelo no propuso un código Python válido. Manteniendo baseline actual.", file=sys.stderr)
            continue
            
        # Guardar el código propuesto
        with open("infer.py", "w", encoding="utf-8") as f:
            f.write(proposed_code)
            
        # Validar sintaxis del código propuesto antes de evaluarlo
        try:
            subprocess.run([python_exe, "-m", "py_compile", "infer.py"], check=True, capture_output=True)
        except subprocess.CalledProcessError as err:
            print("Error: El código propuesto tiene errores de sintaxis Python. Aplicando reversión.", file=sys.stderr)
            print(err.stderr.decode("utf-8", errors="ignore"), file=sys.stderr)
            # Reversión inmediata
            with open("infer.py", "w", encoding="utf-8") as f:
                f.write(current_code)
            continue
            
        # Calcular el nuevo score de precisión
        print("Evaluando el código propuesto con eval_zohar.py...")
        new_score = get_current_score(python_exe)
        print(f"Nuevo Score obtenido: {new_score:.4f}")
        
        # Comparar scores y aplicar lógica de reversión o actualización
        if new_score > best_score:
            print(f"¡Éxito! El score mejoró de {best_score:.4f} a {new_score:.4f}. Guardando nueva base.")
            best_score = new_score
            # Eliminar backup de la iteración anterior porque este es el nuevo baseline
            if os.path.exists(BACKUP_PATH):
                os.remove(BACKUP_PATH)
        else:
            print(f"El score propuesto ({new_score:.4f}) es menor o igual al mejor score ({best_score:.4f}).")
            print("Aplicando reversión al código anterior de infer.py.")
            with open("infer.py", "w", encoding="utf-8") as f:
                f.write(current_code)
                
    # Limpieza final del backup
    if os.path.exists(BACKUP_PATH):
        os.remove(BACKUP_PATH)
        
    print(f"\n=== BUCLE RSI COMPLETADO ===")
    print(f"Precisión inicial: {initial_score:.4f}")
    print(f"Precisión final: {best_score:.4f}")

if __name__ == "__main__":
    main()
