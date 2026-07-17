import os
import sys
import json
import subprocess
import unicodedata
import re

def normalize_str(s) -> str:
    """
    Normaliza un string para comparación tolerante:
    - Minúsculas
    - Quita acentos/diacríticos
    - Elimina caracteres especiales no alfanuméricos
    - Colapsa espacios múltiples
    """
    if not s:
        return ""
    s = str(s).lower().strip()
    # Quitar acentos
    s = "".join(
        c for c in unicodedata.normalize('NFD', s)
        if unicodedata.category(c) != 'Mn'
    )
    # Quitar puntuación y caracteres especiales no alfanuméricos
    s = re.sub(r'[^a-z0-9\s]', '', s)
    # Colapsar espacios
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def calculate_item_score(ground_truth: dict, extracted: dict) -> float:
    """
    Calcula el score de precisión (0.0 a 1.0) para un proyecto individual.
    Llaves evaluadas: Clave, Promovente, Localidad, Municipio, Estado, Tipo_MIA.
    Lógica de scoring por llave:
    - 0.5 puntos si la llave existe.
    - 0.5 puntos adicionales si el valor coincide de forma normalizada.
    """
    keys = ["Clave", "Promovente", "Localidad", "Municipio", "Estado", "Tipo_MIA"]
    total_points = 0.0
    max_points = len(keys) * 1.0  # 6.0
    
    for key in keys:
        if key in extracted:
            total_points += 0.5
            gt_val = normalize_str(ground_truth.get(key, ""))
            ext_val = normalize_str(extracted.get(key, ""))
            
            # Comparación flexible: coincidencia exacta o contenedor
            if gt_val == ext_val:
                total_points += 0.5
            elif gt_val and ext_val and (gt_val in ext_val or ext_val in gt_val):
                # Coincidencia parcial cercana (substring)
                total_points += 0.3
                
    return total_points / max_points

def main():
    gt_path = "dataset_ground_truth.json"
    if not os.path.exists(gt_path):
        print(f"Error: {gt_path} no encontrado.", file=sys.stderr)
        sys.exit(1)
        
    with open(gt_path, "r", encoding="utf-8") as f:
        ground_truth_list = json.load(f)
        
    total_score = 0.0
    count = 0
    
    # Determinar el ejecutable de python correcto (usar el del venv si existe)
    python_exe = ".venv/bin/python" if os.path.exists(".venv/bin/python") else "python"
    
    for gt in ground_truth_list:
        clave = gt.get("Clave")
        if not clave:
            continue
            
        print(f"Evaluando clave: {clave}...", file=sys.stderr)
        
        # Ejecutar infer.py
        try:
            res = subprocess.run(
                [python_exe, "infer.py", "--clave", clave],
                capture_output=True,
                text=True,
                timeout=120.0
            )
            
            # Parsear salida
            extracted = {}
            if res.returncode == 0 and res.stdout.strip():
                try:
                    # Encontrar el bloque JSON en stdout
                    stdout_clean = res.stdout.strip()
                    json_start = stdout_clean.find("{")
                    json_end = stdout_clean.rfind("}")
                    if json_start != -1 and json_end != -1:
                        json_str = stdout_clean[json_start:json_end+1]
                        extracted = json.loads(json_str)
                    else:
                        extracted = json.loads(stdout_clean)
                except Exception as e:
                    print(f"Warning: Fallo al parsear JSON devuelto por infer.py para {clave}: {e}", file=sys.stderr)
                    print(f"Stdout crudo: {res.stdout}", file=sys.stderr)
            else:
                print(f"Warning: infer.py falló con código de salida {res.returncode}", file=sys.stderr)
                print(f"Stderr: {res.stderr}", file=sys.stderr)
                
            item_score = calculate_item_score(gt, extracted)
            print(f"  Score obtenido para {clave}: {item_score:.4f}", file=sys.stderr)
            total_score += item_score
            count += 1
            
        except subprocess.TimeoutExpired:
            print(f"Error: Timeout de infer.py para clave {clave}", file=sys.stderr)
            count += 1
        except Exception as e:
            print(f"Error ejecutando infer.py para clave {clave}: {e}", file=sys.stderr)
            count += 1
            
    final_score = total_score / max(count, 1)
    print(f"\nSCORE: {final_score:.4f}")

if __name__ == "__main__":
    main()
