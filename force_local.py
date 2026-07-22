import re

with open('core/llm_client.py', 'r') as f:
    content = f.read()

# Reescribimos la función de detección para que solo devuelva el servidor local
new_detect = '''def detect_active_backend() -> tuple[str, str]:
    return "llama-server", "gemma-4-e2b"
'''

content = re.sub(
    r'def detect_active_backend\(\) -> tuple\[str, str\]:.*?return "heuristic", "fallback_heuristic"', 
    new_detect, 
    content, 
    flags=re.DOTALL
)

with open('core/llm_client.py', 'w') as f:
    f.write(content)

print("✅ Backend forzado exitosamente a llama-server (Gemma E2B)")
