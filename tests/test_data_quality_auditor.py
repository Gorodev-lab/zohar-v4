import pytest
import pandas as pd
import json
import os
from pathlib import Path
from dw.data_quality_auditor import DataQualityAuditor

def test_auditor_projects_valid_and_invalid():
    """Verifica que el auditor valide correctamente proyectos correctos e incorrectos."""
    auditor = DataQualityAuditor(report_path="dw/test_audit_report.md")
    
    data = [
        # Proyecto totalmente válido
        {
            "clave": "09PU2026I0001",
            "project_name": "Proyecto Válido Puebla",
            "status": "En evaluación",
            "state": "Puebla",
            "year": 2026,
            "files_downloaded": ["estudio", "resumen"],
            "promovente": "Empresa A"
        },
        # Clave con formato inválido
        {
            "clave": "INVALIDA_CLAVE",
            "project_name": "Proyecto Clave Inválida",
            "status": "En evaluación",
            "state": "Veracruz",
            "year": 2026,
            "files_downloaded": [],
            "promovente": "Empresa B"
        },
        # Año fuera de rango (ej. 1980)
        {
            "clave": "09PU1980I0002",
            "project_name": "Proyecto Año Antiguo",
            "status": "Resuelto",
            "state": "Puebla",
            "year": 1980,
            "files_downloaded": [],
            "promovente": "Empresa C"
        },
        # Inconsistencia de año entre clave (2025) y campo year (2026)
        {
            "clave": "09PU2025I0003",
            "project_name": "Proyecto Inconsistente Año",
            "status": "Resuelto",
            "state": "Puebla",
            "year": 2026,
            "files_downloaded": [],
            "promovente": "Empresa D"
        },
        # Estado inválido
        {
            "clave": "09PU2026I0004",
            "project_name": "Proyecto Estado Inválido",
            "status": "Resuelto",
            "state": "EstadoFantasia",
            "year": 2026,
            "files_downloaded": [],
            "promovente": "Empresa E"
        },
        # Faltan campos requeridos (status vacío)
        {
            "clave": "09PU2026I0005",
            "project_name": "Proyecto Sin Estatus",
            "status": "",
            "state": "Puebla",
            "year": 2026,
            "files_downloaded": [],
            "promovente": "Empresa F"
        }
    ]
    df = pd.DataFrame(data)
    
    cleaned_df, metrics = auditor.audit_semarnat_projects(df)
    
    # Solo el primer proyecto debería sobrevivir
    assert len(cleaned_df) == 1
    assert cleaned_df.loc[0, "clave"] == "09PU2026I0001"
    
    # Verificar métricas
    assert metrics["total_rows"] == 6
    assert metrics["rows_removed"] == 5
    
    # Verificar alertas generadas
    assert len(auditor.alerts) > 0
    # Comprobar que hay alertas críticas
    critical_alerts = [a for a in auditor.alerts if a["nivel"] == "CRITICAL"]
    assert len(critical_alerts) >= 5

    # Limpiar archivos de prueba
    if os.path.exists("dw/test_audit_report.md"):
        os.unlink("dw/test_audit_report.md")


def test_auditor_evaluations_valid_and_invalid():
    """Verifica que el auditor filtre adecuadamente inferencias de IA inválidas o fuera de rango."""
    auditor = DataQualityAuditor(report_path="dw/test_audit_report.md")
    
    eval_data = [
        # Evaluación totalmente válida
        {
            "clave": "09PU2026I0001",
            "veredicto": "VIABLE",
            "score": 0.85,
            "confianza_pct": 90,
            "knockouts": "[]",
            "yes_signals": "[]",
            "no_signals": "[]",
            "condicionantes": "[]"
        },
        # Veredicto inválido
        {
            "clave": "09PU2026I0002",
            "veredicto": "SUPER_VIABLE",
            "score": 0.85,
            "confianza_pct": 90,
            "knockouts": "[]",
            "yes_signals": "[]",
            "no_signals": "[]",
            "condicionantes": "[]"
        },
        # Score fuera de rango (mayor a 1.0)
        {
            "clave": "09PU2026I0003",
            "veredicto": "CONDICIONADO",
            "score": 1.5,
            "confianza_pct": 80,
            "knockouts": "[]",
            "yes_signals": "[]",
            "no_signals": "[]",
            "condicionantes": "[]"
        },
        # Confianza fuera de rango (mayor a 100)
        {
            "clave": "09PU2026I0004",
            "veredicto": "NO_VIABLE",
            "score": 0.1,
            "confianza_pct": 120,
            "knockouts": "[]",
            "yes_signals": "[]",
            "no_signals": "[]",
            "condicionantes": "[]"
        }
    ]
    df_evals = pd.DataFrame(eval_data)
    
    cleaned_evals, metrics = auditor.audit_project_evaluations(df_evals)
    
    # Solo la primera evaluación es válida
    assert len(cleaned_evals) == 1
    assert cleaned_evals.loc[0, "clave"] == "09PU2026I0001"
    
    # Verificar métricas
    assert metrics["total_rows"] == 4
    assert metrics["rows_removed"] == 3
    
    # Comprobar que las alertas de evaluación se agregaron a la lista global
    eval_alerts = [a for a in auditor.alerts if a["campo"] in ("veredicto", "score", "confianza_pct")]
    assert len(eval_alerts) == 3

    # Limpiar archivos de prueba
    if os.path.exists("dw/test_audit_report.md"):
        os.unlink("dw/test_audit_report.md")
