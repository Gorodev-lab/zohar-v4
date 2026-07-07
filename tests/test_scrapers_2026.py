"""
tests/test_scrapers_2026.py
Pruebas de inicialización y configuración (sin red, sin Chrome).
Valida URLs y filtros de año para 2026.
"""

import pytest
from pathlib import Path


# ===========================================================================
# GazetteScraper — URL 2026
# ===========================================================================

def test_gazette_scraper_2026_url():
    """
    GazetteScraper genera URL correcta para año 2026.

    CONTRATO:
        "ai=2026" in url  ✓
        "sinat.semarnat.gob.mx" in url  ✓
    """
    from scrapers.gazette_scraper import GazetteScraper

    scraper = GazetteScraper(output_dir="/tmp/test_gacetas_2026")
    url = scraper.get_iframe_url(2026)

    assert "ai=2026" in url, (
        f"URL no contiene 'ai=2026': {url}"
    )
    assert "sinat.semarnat.gob.mx" in url, (
        f"URL no apunta a sinat.semarnat.gob.mx: {url}"
    )


def test_gazette_scraper_iframe_url_format():
    """La URL del iframe incluye el año correctamente formateado."""
    from scrapers.gazette_scraper import GazetteScraper

    scraper = GazetteScraper()
    for year in [2024, 2025, 2026]:
        url = scraper.get_iframe_url(year)
        assert str(year) in url, f"Año {year} no encontrado en URL: {url}"


def test_gazette_scraper_output_dir_creation(tmp_path):
    """GazetteScraper crea el directorio de salida al inicializarse."""
    out_dir = tmp_path / "gacetas_test"
    from scrapers.gazette_scraper import GazetteScraper
    scraper = GazetteScraper(output_dir=str(out_dir))
    assert out_dir.exists(), f"Directorio no creado: {out_dir}"


# ===========================================================================
# ASEAScraper — Inicialización 2026
# ===========================================================================

def test_asea_scraper_2026_initialization():
    """
    ASEAScraper guarda year_filter y output_dir correctamente.

    CONTRATO:
        scraper.year_filter == 2026  ✓
        scraper.output_dir.name == "asea"  ✓
    """
    from scrapers.asea_scraper import ASEAScraper

    scraper = ASEAScraper(
        output_dir="downloads/asea",
        year_filter=2026,
    )

    assert scraper.year_filter == 2026, (
        f"year_filter esperado 2026, obtenido {scraper.year_filter}"
    )
    assert scraper.output_dir.name == "asea", (
        f"output_dir.name esperado 'asea', obtenido '{scraper.output_dir.name}'"
    )


def test_asea_scraper_no_year_filter():
    """Sin year_filter, ASEAScraper acepta todos los años."""
    from scrapers.asea_scraper import ASEAScraper

    scraper = ASEAScraper(output_dir="downloads/asea")
    assert scraper.year_filter is None


def test_asea_scraper_output_dir_creation(tmp_path):
    """ASEAScraper crea directorio de salida."""
    out_dir = tmp_path / "asea_test"
    from scrapers.asea_scraper import ASEAScraper
    scraper = ASEAScraper(output_dir=str(out_dir))
    assert out_dir.exists()


def test_asea_scraper_index_url_defined():
    """ASEAScraper tiene ASEA_INDEX_URL definida correctamente."""
    from scrapers.asea_scraper import ASEA_INDEX_URL
    assert "asea.gob.mx" in ASEA_INDEX_URL
    assert ASEA_INDEX_URL.startswith("http")


def test_asea_scraper_year_extraction():
    """ASEAScraper extrae el año de texto y URLs correctamente."""
    from scrapers.asea_scraper import ASEAScraper

    scraper = ASEAScraper()
    assert scraper._extract_year("Gaceta ASEA 2026 - Enero") == 2026
    assert scraper._extract_year("gaceta_2025_03.pdf") == 2025
    assert scraper._extract_year("sin-anio.pdf") is None
