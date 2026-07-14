#!/usr/bin/env python3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import logging
logging.basicConfig(level=logging.INFO)

from core.pdf_processor import iter_pages_as_markdown

pdf_path = BASE_DIR / "data/harness_test/temp_dl/resumen.09_MG-0006_01_26.pdf"
if not pdf_path.exists():
    print(f"Error: {pdf_path} does not exist.")
    sys.exit(1)

print(f"Testing OCR on {pdf_path.name}...")
for page_num, total, text, is_scanned in iter_pages_as_markdown(pdf_path):
    print(f"Page {page_num}/{total} - Scanned: {is_scanned} - Text length: {len(text)}")
    print("First 200 chars:")
    print(text[:200])
    print("-" * 50)
