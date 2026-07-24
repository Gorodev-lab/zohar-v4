import re

filepath = "scrapers/semarnat_downloader.py"
with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# Reemplazar la búsqueda estricta de texto por una búsqueda profunda tolerante a espacios
nuevo_xpath = "error_xpath = \"//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'encontro informaci')]\""

content = re.sub(
    r"error_xpath\s*=\s*\".*?\"",
    nuevo_xpath,
    content
)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("✅ Escudo del modal reforzado y tolerante a espacios HTML.")
