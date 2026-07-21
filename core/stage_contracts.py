"""
core/stage_contracts.py
Matriz de Validación por Contrato de Etapa para Zohar v4.
Verifica la integridad de cada fase (DOM -> OCR -> DW -> Vault -> LLM).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


def validate_dom_extraction(metadata: Dict[str, Any]) -> bool:
    """Contrato Etapa 1: Ingesta DOM contiene clave y metadatos básicos."""
    if not isinstance(metadata, dict):
        return False
    # Requiere al menos que exista o bien proyecto o promovente o ubicación
    has_info = any(metadata.get(k) for k in ["project_name", "promovente", "state", "municipio"])
    return has_info


def validate_markdown_extraction(md_path: Path) -> bool:
    """Contrato Etapa 2: El archivo Markdown extraído existe y no está vacío (>80 chars)."""
    if not md_path or not Path(md_path).exists():
        return False
    try:
        content = Path(md_path).read_text(encoding="utf-8", errors="ignore")
        return len(content.strip()) >= 80
    except Exception as exc:
        logger.warning("Error validando Markdown %s: %s", md_path, exc)
        return False


def validate_dw_record(clave: str) -> bool:
    """Contrato Etapa 3: El registro existe en PostgreSQL o CSV."""
    if not clave:
        return False
    try:
        import os
        import sqlalchemy as sa
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/maritime_dw")
        engine = sa.create_engine(db_url)
        with engine.connect() as conn:
            res = conn.execute(
                sa.text("SELECT COUNT(*) FROM public.semarnat_projects WHERE clave = :c"),
                {"c": clave}
            ).scalar()
            if res and res > 0:
                return True
    except Exception:
        pass
    return False


def validate_vault_note(base_dir: Path, clave: str) -> bool:
    """Contrato Etapa 4: Existe la nota en second_brain/02_Entities/Proyecto - {clave}.md."""
    note_path = Path(base_dir) / "second_brain" / "02_Entities" / f"Proyecto - {clave}.md"
    return note_path.exists() and note_path.stat().st_size > 100


def validate_pipeline_contract_all(base_dir: Path, clave: str, metadata: dict = None, md_path: Path = None) -> Dict[str, bool]:
    """
    Verifica los contratos de todas las etapas para una clave dada.
    Retorna mapa de {"dom": bool, "ocr": bool, "dw": bool, "vault": bool}.
    """
    return {
        "dom": validate_dom_extraction(metadata or {}),
        "ocr": validate_markdown_extraction(md_path) if md_path else False,
        "dw": validate_dw_record(clave),
        "vault": validate_vault_note(base_dir, clave),
    }
