# tests/conftest.py
# Registro de marks personalizados para evitar PytestUnknownMarkWarning

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "headful: test que requiere Chrome en modo visual (no headless)"
    )
    config.addinivalue_line(
        "markers", "live: test que requiere conexión a internet y portal SEMARNAT activo"
    )
