import json, re
from pathlib import Path

GROUND_TRUTH = Path("dataset_ground_truth.json")
EXTRACTIONS_DIR = Path("extractions")  # ajusta si tus .md viven en otro lado

_ESTADOS_MX = ["Aguascalientes","Baja California Sur","Baja California","Campeche",
"Chiapas","Chihuahua","Coahuila","Colima","Ciudad de México","Durango","Guanajuato",
"Guerrero","Hidalgo","Jalisco","México","Michoacán","Morelos","Nayarit","Nuevo León",
"Oaxaca","Puebla","Querétaro","Quintana Roo","San Luis Potosí","Sinaloa","Sonora",
"Tabasco","Tamaulipas","Tlaxcala","Veracruz","Yucatán","Zacatecas"]
_KEYWORD_PATTERNS = [r"promovente[:\s]", r"solicitante[:\s]", r"raz[oó]n social[:\s]",
r"nombre\s+de\s+la\s+empresa", r"responsable\s+del\s+proyecto", r"ubicad[oa]\s+en",
r"municipio\s+de", r"localidad\s+de", r"sector\s+(productivo|econ[oó]mico)?"]

def naive_snippet(text, n=2000):
    return text[:n]

def targeted_snippet(text, prefix=2000, window=220, max_total=5000):
    if not text: return ""
    pieces, seen = [text[:prefix]], [(0, prefix)]
    pattern = re.compile("(" + "|".join(_KEYWORD_PATTERNS) + "|" +
                          "|".join(re.escape(e) for e in _ESTADOS_MX) + ")", re.IGNORECASE)
    for m in pattern.finditer(text):
        s, e = max(0, m.start()-60), min(len(text), m.end()+window)
        if any(s < se and e > ss for ss, se in seen): continue
        pieces.append(text[s:e]); seen.append((s, e))
        if sum(len(p) for p in pieces) >= max_total: break
    return "\n[...]\n".join(pieces)[:max_total]

def contains_ci(haystack, needle):
    if not needle or needle in ("Desconocido", "No especificado"): return None
    return needle.lower() in haystack.lower()

def main():
    gt = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    results = {"naive": {"promovente": [], "estado": []}, "targeted": {"promovente": [], "estado": []}}
    for item in gt:
        clave = item["Clave"]
        candidates = sorted(EXTRACTIONS_DIR.glob(f"{clave}*.md"),
                             key=lambda p: p.stat().st_size, reverse=True)
        if not candidates:
            print(f"SKIP {clave}: no se encontró ningún .md con ese prefijo en {EXTRACTIONS_DIR}")
            continue
        md_path = candidates[0]
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        for name, fn in [("naive", naive_snippet), ("targeted", targeted_snippet)]:
            snippet = fn(text)
            hp, he = contains_ci(snippet, item.get("Promovente")), contains_ci(snippet, item.get("Estado"))
            if hp is not None: results[name]["promovente"].append(hp)
            if he is not None: results[name]["estado"].append(he)
    for name, fields in results.items():
        print(f"\n=== {name} ===")
        for field, hits in fields.items():
            if hits:
                rate = sum(hits) / len(hits) * 100
                print(f"  {field}: {sum(hits)}/{len(hits)} ({rate:.1f}%)")
            else:
                print(f"  {field}: sin muestras válidas")

if __name__ == "__main__":
    main()
