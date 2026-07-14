"""
tests/test_sinat_downloader_harness.py
HARNESS PRINCIPAL — Contratos inmutables del clasificador posicional.

Claves SINAT confirmadas (verdad de campo):
    3_buttons: 21PU2025H0155  → bitacora: 09/MP-0586/12/25
    2_buttons: 05CO2026I0001  → bitacora: 09/MG-0006/01/26
    1_button:  19NL2025H0134  → bitacora: 09/MP-0637/12/25
"""

import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Claves SINAT confirmadas (NO modificar)
# ---------------------------------------------------------------------------
KNOWN_KEYS = {
    "3_buttons": {
        "clave":          "21PU2025H0155",
        "bitacora":       "09/MP-0586/12/25",
        "expected_types": ["resumen", "estudio", "resolutivo"],
    },
    "2_buttons": {
        "clave":          "05CO2026I0001",
        "bitacora":       "09/MG-0006/01/26",
        "expected_types": ["resumen", "resolutivo"],
    },
    "1_button": {
        "clave":          "19NL2025H0134",
        "bitacora":       "09/MP-0637/12/25",
        "expected_types": ["resumen"],
    },
}

# Directorios del harness
HARNESS_BASE    = Path("data/harness_test")
RESUMENES_DIR   = HARNESS_BASE / "resumenes"
ESTUDIOS_DIR    = HARNESS_BASE / "estudios"
RESOLUTIVOS_DIR = HARNESS_BASE / "resolutivos"
TEMP_DIR        = HARNESS_BASE / "temp"


# ===========================================================================
# TestClassificationHeuristic — Contratos inmutables
# ===========================================================================

class TestClassificationHeuristic:
    """
    Valida el clasificador posicional renombrar_archivos_por_clave().

    REGLA DE ORO:
        n=2 → resumen (idx 0) + resolutivo (idx 1)
        El caso de 2 archivos NO asigna estudio al índice 1.
        Ese fue el bug original. El harness valida explícitamente estudios == [].
    """

    def _create_dummy_pdfs(self, tmp_path: Path, n: int) -> list[Path]:
        """Crea n PDFs sintéticos con nombres genéricos (sin keywords)."""
        pdfs = []
        base_ts = time.time() - 1  # Anterior al since_ts
        for i in range(n):
            pdf = tmp_path / f"documento_{i:02d}.pdf"
            pdf.write_bytes(b"%PDF-1.4 dummy content for testing")
            # Ajustar mtime para que sea anterior al since_ts
            import os
            os.utime(pdf, (base_ts - 10, base_ts - 10))
            pdfs.append(pdf)
        return pdfs

    def _touch_pdfs_after(self, pdfs: list[Path], since_ts: float):
        """Actualiza los mtime de los PDFs para que sean posteriores a since_ts."""
        import os
        new_ts = since_ts + 1
        for pdf in pdfs:
            os.utime(pdf, (new_ts, new_ts))

    def test_classification_2_files_maps_to_resumen_and_resolutivo(self, tmp_path):
        """
        2 PDFs sin keywords → resumen + resolutivo (NO estudio).

        CONTRATO INMUTABLE:
            result["resumenes"]   → len == 1  ✓
            result["estudios"]    → len == 0  ✓  ← regresión si falla
            result["resolutivos"] → len == 1  ✓
        """
        from scrapers.semarnat_downloader import renombrar_archivos_por_clave

        clave = KNOWN_KEYS["2_buttons"]["clave"]
        download_dir = tmp_path / "dl"
        download_dir.mkdir()
        resumenes_dir  = tmp_path / "resumenes"
        resolutivos_dir = tmp_path / "resolutivos"
        estudios_dir   = tmp_path / "estudios"

        # Crear 2 PDFs genéricos
        pdfs = self._create_dummy_pdfs(download_dir, 2)
        since_ts = time.time() - 5
        self._touch_pdfs_after(pdfs, since_ts)

        result = renombrar_archivos_por_clave(
            download_dir=download_dir,
            clave=clave,
            since_ts=since_ts,
            carpeta_estudios=estudios_dir,
            carpeta_resolutivos=resolutivos_dir,
            carpeta_resumenes=resumenes_dir,
        )

        # CONTRATOS
        assert len(result["resumenes"])   == 1, (
            f"Esperado 1 resumen, obtenido {len(result['resumenes'])}"
        )
        assert len(result["estudios"])    == 0, (
            f"BUG: 2 archivos NO debe asignar estudio. "
            f"Obtenido {len(result['estudios'])} estudios. "
            f"Revisa POSITIONAL_CLASSIFICATION[2]."
        )
        assert len(result["resolutivos"]) == 1, (
            f"Esperado 1 resolutivo, obtenido {len(result['resolutivos'])}"
        )

    def test_classification_3_files_maps_correctly(self, tmp_path):
        """
        3 PDFs sin keywords → resumen + estudio + resolutivo.

        CONTRATO INMUTABLE:
            result["resumenes"]   → len == 1  ✓
            result["estudios"]    → len == 1  ✓
            result["resolutivos"] → len == 1  ✓
        """
        from scrapers.semarnat_downloader import renombrar_archivos_por_clave

        clave = KNOWN_KEYS["3_buttons"]["clave"]
        download_dir = tmp_path / "dl"
        download_dir.mkdir()
        resumenes_dir   = tmp_path / "resumenes"
        resolutivos_dir = tmp_path / "resolutivos"
        estudios_dir    = tmp_path / "estudios"

        # Crear 3 PDFs genéricos
        pdfs = self._create_dummy_pdfs(download_dir, 3)
        since_ts = time.time() - 5
        self._touch_pdfs_after(pdfs, since_ts)

        result = renombrar_archivos_por_clave(
            download_dir=download_dir,
            clave=clave,
            since_ts=since_ts,
            carpeta_estudios=estudios_dir,
            carpeta_resolutivos=resolutivos_dir,
            carpeta_resumenes=resumenes_dir,
        )

        # CONTRATOS
        assert len(result["resumenes"])   == 1, (
            f"Esperado 1 resumen, obtenido {len(result['resumenes'])}"
        )
        assert len(result["estudios"])    == 1, (
            f"Esperado 1 estudio, obtenido {len(result['estudios'])}"
        )
        assert len(result["resolutivos"]) == 1, (
            f"Esperado 1 resolutivo, obtenido {len(result['resolutivos'])}"
        )

    def test_classification_1_file_maps_to_estudio(self, tmp_path):
        """1 PDF sin keywords → estudio (índice 0 cuando n=1)."""
        from scrapers.semarnat_downloader import renombrar_archivos_por_clave

        clave = KNOWN_KEYS["1_button"]["clave"]
        download_dir = tmp_path / "dl"
        download_dir.mkdir()
        estudios_dir = tmp_path / "estudios"

        pdfs = self._create_dummy_pdfs(download_dir, 1)
        since_ts = time.time() - 5
        self._touch_pdfs_after(pdfs, since_ts)

        result = renombrar_archivos_por_clave(
            download_dir=download_dir,
            clave=clave,
            since_ts=since_ts,
            carpeta_estudios=estudios_dir,
        )

        assert len(result["estudios"])    == 1
        assert len(result["resumenes"])   == 0
        assert len(result["resolutivos"]) == 0

    def test_classification_keyword_override(self, tmp_path):
        """Si el nombre contiene keyword, prevalece sobre posición."""
        from scrapers.semarnat_downloader import renombrar_archivos_por_clave
        import os

        clave = "TEST_KW"
        download_dir = tmp_path / "dl"
        download_dir.mkdir()
        resumenes_dir = tmp_path / "r"
        estudios_dir  = tmp_path / "e"

        since_ts = time.time() - 5

        # Archivo con keyword "resolutivo" en nombre
        pdf = download_dir / "resolutivo_definitivo.pdf"
        pdf.write_bytes(b"%PDF test")
        os.utime(pdf, (since_ts + 1, since_ts + 1))

        result = renombrar_archivos_por_clave(
            download_dir=download_dir,
            clave=clave,
            since_ts=since_ts,
            carpeta_estudios=estudios_dir,
            carpeta_resumenes=resumenes_dir,
        )

        # Debe clasificar como resolutivo por keyword, aunque posición diría estudio
        assert len(result["resolutivos"]) == 1
        assert len(result["estudios"])    == 0

    def test_classification_empty_dir_returns_empty(self, tmp_path):
        """Directorio vacío retorna listas vacías."""
        from scrapers.semarnat_downloader import renombrar_archivos_por_clave

        download_dir = tmp_path / "dl"
        download_dir.mkdir()

        result = renombrar_archivos_por_clave(
            download_dir=download_dir,
            clave="EMPTY",
            since_ts=time.time(),
        )

        assert result == {"resumenes": [], "estudios": [], "resolutivos": []}


# ===========================================================================
# TestSINATButtonCount — Requiere Chrome + Internet (marcados headful)
# ===========================================================================

class TestSINATButtonCount:
    """
    Pruebas headful que verifican el número real de botones en SINAT.
    Requieren Chrome instalado y conexión a internet.
    Correr con: pytest -s -k headful

    FIX: El portal SEMARNAT usa Angular con hash routing (#/).
    time.sleep(5) era insuficiente. Se reemplaza con WebDriverWait
    que espera activamente hasta 60s a que aparezca .descargas.
    """

    WAIT_TIMEOUT = 60  # segundos — Angular puede tardar en renderizar

    def _wait_for_buttons(self, driver, css_selector: str, timeout: int = 60, bitacora_value: str = ""):
        """
        Espera con polling hasta que aparezcan botones en .descargas,
        con fallback a XPath si el CSS selector no encuentra nada.
        Si redirige a la página principal, ingresa la clave de bitácora en el input y busca.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from scrapers.semarnat_downloader import BUTTON_XPATH_TEMPLATE
        import time
        import re

        # Esperar que Angular termine de cargar (readyState + ng-version)
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(2)  # micro-pausa para hydration de Angular

        # Intentar extraer la clave de bitácora si no se proveyó
        if not bitacora_value:
            original_url = driver.current_url
            m = re.search(r"consulta-tramite/(09/[A-Z]{2}-\d{4}/\d{2}/\d{2})", original_url)
            if m:
                bitacora_value = m.group(1)
            else:
                parts = original_url.split("consulta-tramite/")
                if len(parts) > 1:
                    bitacora_value = parts[-1].strip()

        # Polling activo hasta que aparezcan los botones
        deadline = time.time() + timeout
        tried_search = False

        while time.time() < deadline:
            buttons = driver.find_elements(By.CSS_SELECTOR, css_selector)
            if buttons:
                return buttons
            # Intentar con XPath alternativo
            xpath = (
                "//div[contains(@class,'descargas')]//button | "
                "//div[contains(@class,'descargas')]//*[contains(@class,'btn')] | "
                "//button[contains(@class,'btn-download')] | "
                "//*[contains(@class,'download')]//button"
            )
            buttons = driver.find_elements(By.XPATH, xpath)
            if buttons:
                return buttons

            # Si no hay botones y no hemos intentado buscar, y está en el portal-consulta sin resultados
            if not tried_search and bitacora_value and (
                "#/portal-consulta" in driver.current_url and "consulta-tramite/" not in driver.current_url
                or not driver.find_elements(By.CSS_SELECTOR, ".descargas")
            ):
                # Intentar localizar el input de búsqueda e ingresar la clave
                search_input = None
                for sel in [
                    (By.CSS_SELECTOR, "input[placeholder*='bitácora']"),
                    (By.CSS_SELECTOR, "input[placeholder*='clave']"),
                    (By.CSS_SELECTOR, "input[type='text']"),
                    (By.XPATH, "//input")
                ]:
                    try:
                        if driver.find_elements(*sel):
                            search_input = driver.find_element(*sel)
                            break
                    except Exception:
                        pass

                if search_input:
                    try:
                        driver.execute_script("arguments[0].click();", search_input)
                        search_input.clear()
                        time.sleep(0.3)
                        search_input.send_keys(bitacora_value)
                        driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", search_input)

                        # Buscar botón
                        search_btn = None
                        for btn_sel in [
                            (By.XPATH, "//button[contains(., 'Buscar')]"),
                            (By.XPATH, "//button[contains(text(), 'Buscar')]"),
                            (By.CSS_SELECTOR, "button"),
                            (By.XPATH, "//*[contains(text(), 'Buscar')]")
                        ]:
                            try:
                                if driver.find_elements(*btn_sel):
                                    search_btn = driver.find_element(*btn_sel)
                                    break
                            except Exception:
                                pass

                        if search_btn:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_btn)
                            time.sleep(0.3)
                            search_btn.click()
                            tried_search = True
                            time.sleep(4)  # esperar recarga
                    except Exception:
                        pass

            time.sleep(1.5)

        # Último intento — devolver lo que haya
        return driver.find_elements(By.CSS_SELECTOR, css_selector)

    @pytest.mark.headful
    def test_key_3_buttons_headful(self, tmp_path):
        """21PU2025H0155 debe tener 3 botones de descarga."""
        from scrapers.semarnat_downloader import make_chrome_driver
        from scrapers.semarnat_downloader import BUTTON_CSS_SELECTOR

        info = KNOWN_KEYS["3_buttons"]
        driver = make_chrome_driver(tmp_path, headless=False)
        try:
            url = (
                "https://app.semarnat.gob.mx/consulta-tramite/"
                f"#/portal-consulta/consulta-tramite/{info['bitacora']}"
            )
            driver.get(url)
            buttons = self._wait_for_buttons(driver, BUTTON_CSS_SELECTOR, self.WAIT_TIMEOUT, info['bitacora'])
            assert len(buttons) == 3, (
                f"Esperado 3 botones, encontrado {len(buttons)}.\n"
                f"URL: {url}\n"
                f"Page title: {driver.title}"
            )
        finally:
            driver.quit()

    @pytest.mark.headful
    @pytest.mark.live
    def test_key_2_buttons_headful(self, tmp_path):
        """05CO2026I0001 debe tener 2 botones de descarga."""
        from scrapers.semarnat_downloader import make_chrome_driver
        from scrapers.semarnat_downloader import BUTTON_CSS_SELECTOR

        info = KNOWN_KEYS["2_buttons"]
        driver = make_chrome_driver(tmp_path, headless=False)
        try:
            url = (
                "https://app.semarnat.gob.mx/consulta-tramite/"
                f"#/portal-consulta/consulta-tramite/{info['bitacora']}"
            )
            driver.get(url)
            buttons = self._wait_for_buttons(driver, BUTTON_CSS_SELECTOR, self.WAIT_TIMEOUT, info['bitacora'])
            assert len(buttons) == 2, (
                f"Esperado 2 botones, encontrado {len(buttons)}.\n"
                f"URL: {url}\n"
                f"Page title: {driver.title}"
            )
        finally:
            driver.quit()
