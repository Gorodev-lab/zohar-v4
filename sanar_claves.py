import re

def sanar_clave_dgira(clave_sucia):
    clave = clave_sucia.strip().upper()
    
    # Si ni siquiera tiene 13 caracteres, no perdemos el tiempo
    if len(clave) != 13:
        return None
        
    # Diccionario de errores comunes de OCR (Letra -> Número)
    ocr_num_fixes = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2'}
    
    # Máscara estricta de SEMARNAT: N=Número, L=Letra
    mascara = "NNLLNNNNLNNNN"
    clave_limpia = ""
    
    for i, char in enumerate(clave):
        if mascara[i] == 'N':
            # Si esperamos un número pero hay una letra confusa, la reparamos
            clave_limpia += ocr_num_fixes.get(char, char)
        elif mascara[i] == 'L':
            # Si esperamos una letra pero hay un número confuso
            if char == '0': clave_limpia += 'O'
            elif char == '1': clave_limpia += 'I'
            elif char == '5': clave_limpia += 'S'
            else: clave_limpia += char
            
    # Validación final post-sanación
    if re.match(r"^[0-9]{2}[A-Z]{2}[0-9]{4}[A-Z][0-9]{4}$", clave_limpia):
        return clave_limpia
    return None

claves_ocr = ["12GE2026I0021", "02BC2026I0026", "O2BC2024E0044", "08CH2O26H0134", "Clave_Basura"]

print("--- RESULTADOS DE AUTO-SANACIÓN ---")
for c in claves_ocr:
    sanada = sanar_clave_dgira(c)
    if sanada:
        estado = "✨ REPARADA" if sanada != c else "✅ INTACTA"
        print(f"{estado}: {c} -> {sanada}")
    else:
        print(f"❌ DESCARTADA (Irrecuperable): {c}")
