import pytest
import os
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from api.main import process_pdf_background

def test_process_pdf_background_full_flow(tmp_path):
    """Verifica que process_pdf_background ejecute la extracción, inferencia e indexación en RAG."""
    # Configurar directorios temporales modificando las constantes de api.main
    downloads_dir = tmp_path / "downloads"
    extractions_dir = tmp_path / "extractions"
    data_dir = tmp_path / "data"
    
    downloads_dir.mkdir()
    extractions_dir.mkdir()
    data_dir.mkdir()
    
    # Crear un PDF de prueba dummy
    pdf_path = downloads_dir / "01AG2026X9999.estudio.00.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n" + "Este es un estudio de impacto ambiental de prueba para verificar la mitigación de vegetación costera en Aguascalientes.".encode("utf-8"))
    
    # Crear mock del broadcaster
    mock_broadcaster = MagicMock()
    
    # Parchear las constantes del módulo api.main para que apunten a nuestras rutas temporales
    with patch("api.main.DOWNLOADS_DIR", downloads_dir), \
         patch("api.main.EXTRACTIONS_DIR", extractions_dir), \
         patch("api.main.DATA_DIR", data_dir), \
         patch("api.main.BASE_DIR", tmp_path), \
         patch("core.rag_engine.DB_URL", "sqlite:///:memory:"): # Evitar tocar DB real
        
        # Ejecutar el procesamiento en background
        process_pdf_background(pdf_path, broadcaster=mock_broadcaster)
        
        # 1. Verificar que se haya creado la extracción Markdown
        md_file = extractions_dir / "01AG2026X9999.estudio.00.md"
        assert md_file.exists()
        md_content = md_file.read_text(encoding="utf-8")
        assert "01AG2026X9999" in md_content
        
        # 2. Verificar que se haya creado el reporte de inferencia
        inference_file = data_dir / "inference_cache" / "01AG2026X9999.json"
        assert inference_file.exists()
        inference_data = json.loads(inference_file.read_text(encoding="utf-8"))
        assert "veredicto" in inference_data
        
        # 3. Verificar que se haya notificado vía broadcaster
        assert mock_broadcaster.broadcast.call_count >= 2
        mock_broadcaster.broadcast.assert_any_call("extractions_updated", "01AG2026X9999.estudio.00.md")
        mock_broadcaster.broadcast.assert_any_call("inferences_updated", "01AG2026X9999.json")
