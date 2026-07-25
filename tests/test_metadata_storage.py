"""Prueba de la capa SQLite de core/metadata_extractor.py (Bloque 1.5 / storage).

Usa una DB temporal: NO toca data/metadata_proyecto.db real ni invoca LLM.
"""
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.metadata_extractor import (
    init_db, upsert_metadata, get_metadata, count_rows,
)


def _tmp_db():
    return Path(tempfile.mkdtemp()) / "t.db"


def test_init_y_upsert_ok():
    db = _tmp_db()
    init_db(db)
    d = {"clave": "04CA2026E0011", "promovente": "Empresa X", "municipio": "Carmen",
         "estado": "Campeche", "localidad": "Palizada", "descripcion_proyecto": "Planta solar.",
         "confianza_extraccion": "alta", "campos_faltantes": []}
    r = upsert_metadata("04CA2026E0011", d, snippet_fuente="snip", db_path=db)
    assert r["escrito"] is True
    assert r["requiere_revision"] is False
    assert count_rows(db) == 1
    row = get_metadata("04CA2026E0011", db)
    assert row["estado"] == "Campeche"
    assert row["requiere_revision"] == 0


def test_no_escritura_clave_invalida():
    db = _tmp_db()
    init_db(db)
    d = {"clave": "BADKEY", "promovente": "Y", "municipio": "Z", "estado": "Sonora",
         "localidad": None, "descripcion_proyecto": None, "confianza_extraccion": "baja",
         "campos_faltantes": ["promovente"]}
    r = upsert_metadata("BADKEY", d, db_path=db)
    assert r["escrito"] is False
    assert "patrón" in (r["razon"] or "")
    assert count_rows(db) == 0


def test_no_escritura_estado_fuera_lista():
    db = _tmp_db()
    init_db(db)
    d = {"clave": "05CO2026E0014", "promovente": "W", "municipio": "Q", "estado": "Atlantis",
         "localidad": None, "descripcion_proyecto": "X", "confianza_extraccion": "media",
         "campos_faltantes": []}
    r = upsert_metadata("05CO2026E0014", d, db_path=db)
    assert r["escrito"] is False
    assert "32 entidades" in (r["razon"] or "")
    assert count_rows(db) == 0


def test_confianza_baja_marca_revision():
    db = _tmp_db()
    init_db(db)
    d = {"clave": "03BS2024U0025", "promovente": "Julio Cesar Manjarrez Robles",
         "municipio": "X", "estado": "Baja California Sur", "localidad": None,
         "descripcion_proyecto": "Proyecto Vista Cerralvo.", "confianza_extraccion": "baja",
         "campos_faltantes": ["localidad", "descripcion_proyecto"]}
    r = upsert_metadata("03BS2024U0025", d, db_path=db)
    assert r["escrito"] is True
    assert r["requiere_revision"] is True
    row = get_metadata("03BS2024U0025", db)
    assert row["requiere_revision"] == 1


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
