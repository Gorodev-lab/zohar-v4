import os
import re
import json
import pandas as pd
from datetime import datetime
from typing import Dict, Any, Tuple, List

class DataQualityAuditor:
    """
    Pandas-based auditor that checks dataset health, enforces data types, 
    verifies ranges/SLA, and flags issues for SEMARNAT projects.
    """
    def __init__(self, report_path: str = "dw/audit_report.md"):
        self.report_path = report_path
        self.json_report_path = "dw/audit_report.json"
        self.all_metrics = {}
        self.alerts: List[Dict[str, Any]] = []
        
        # Create/Clear report on init
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write(f"# Reporte de Auditoría de Calidad de Datos (SEMARNAT)\n")
            f.write(f"Generado el: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # Clear JSON report on init
        self._save_json_report()

    def log_section(self, title: str):
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"## {title}\n\n")

    def _save_json_report(self):
        # We save alerts inside self.all_metrics["alerts"] for backward compatibility
        self.all_metrics["alerts"] = self.alerts
        with open(self.json_report_path, "w", encoding="utf-8") as f:
            json.dump(self.all_metrics, f, indent=2, ensure_ascii=False)

    def audit_semarnat_projects(
        self, 
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Runs specific quality audits on SEMARNAT projects:
        1. Clave format validation (regex).
        2. Year consistency (clave year matching metadata year).
        3. Year range check (1990 to current_year + 1).
        4. State consistency check (matches official Mexican states).
        5. Missing required fields (clave, project_name, status).
        6. SLA check (warning if status is 'En evaluación' but MIA study is missing).
        """
        name = "SEMARNAT Projects"
        total_rows = len(df)
        metrics = {
            "dataset_name": name,
            "total_rows": total_rows,
            "missing_values": {},
            "type_errors": {},
            "range_violations": 0,  # Used for SLA violations
            "regex_violations": 0,  # Used for Clave, Year, and State format errors
            "duplicate_rows": 0,
            "rows_removed": 0
        }

        if total_rows == 0:
            self._write_empty_audit(name)
            return df, metrics

        # 1. Duplicate check (by clave)
        duplicates = df["clave"].duplicated().sum() if "clave" in df.columns else 0
        metrics["duplicate_rows"] = int(duplicates)

        # 2. Missing values check
        required_cols = ["clave", "project_name", "status"]
        for col in df.columns:
            missing_count = df[col].isnull().sum()
            if missing_count > 0:
                pct = (missing_count / total_rows) * 100
                metrics["missing_values"][col] = {
                    "count": int(missing_count),
                    "percentage": round(pct, 2)
                }

        # 3. Validation masks
        critical_missing_mask = pd.Series(False, index=df.index)
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
            critical_missing_mask |= df[col].isnull() | (df[col].astype(str).str.strip() == "")

        # 4. Regex / Format checks on Clave
        clave_pattern = re.compile(r"^\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5}$")
        regex_violations_mask = pd.Series(False, index=df.index)
        
        try:
            from core.graph_builder import ESTADO_NOMBRES
        except ImportError:
            ESTADO_NOMBRES = {}

        valid_state_names = {name.lower(): name for name in ESTADO_NOMBRES.values()}
        valid_state_keys = {key.upper() for key in ESTADO_NOMBRES.keys()}
        current_year = datetime.now().year

        for idx, row in df.iterrows():
            clave = str(row.get("clave", "")).strip().upper()
            
            # Check missing critical fields first
            for col in required_cols:
                val = row.get(col)
                if pd.isnull(val) or str(val).strip() == "":
                    self.alerts.append({
                        "clave": clave if clave else f"ROW_{idx}",
                        "campo": col,
                        "tipo_error": "Campo requerido faltante",
                        "nivel": "CRITICAL",
                        "mensaje": f"El campo obligatorio '{col}' está vacío o es nulo."
                    })
            
            if not clave_pattern.match(clave) or clave.endswith("9999") or "MOCK" in clave or "TEST" in clave:
                regex_violations_mask.at[idx] = True
                metrics["regex_violations"] += 1
                self.alerts.append({
                    "clave": clave,
                    "campo": "clave",
                    "tipo_error": "Clave mock o formato inválido",
                    "nivel": "CRITICAL",
                    "mensaje": f"La clave '{clave}' no cumple el formato válido o es un registro de prueba/mock."
                })
            else:
                # 5. Year consistency check
                clave_year = clave[4:8]
                row_year = str(row.get("year", ""))
                if clave_year not in row_year:
                    regex_violations_mask.at[idx] = True
                    metrics["regex_violations"] += 1
                    self.alerts.append({
                        "clave": clave,
                        "campo": "year",
                        "tipo_error": "Inconsistencia de año",
                        "nivel": "CRITICAL",
                        "mensaje": f"El año de la clave ({clave_year}) no coincide con el año del registro ({row_year})."
                    })
                
                # Check year range
                try:
                    yr_val = int(row.get("year", 0))
                    if yr_val < 1990 or yr_val > current_year + 1:
                        regex_violations_mask.at[idx] = True
                        metrics["regex_violations"] += 1
                        self.alerts.append({
                            "clave": clave,
                            "campo": "year",
                            "tipo_error": "Rango de año inválido",
                            "nivel": "CRITICAL",
                            "mensaje": f"El año {yr_val} está fuera del rango permitido [1990, {current_year + 1}]."
                        })
                except Exception:
                    regex_violations_mask.at[idx] = True
                    metrics["regex_violations"] += 1
                    self.alerts.append({
                        "clave": clave,
                        "campo": "year",
                        "tipo_error": "Año no numérico",
                        "nivel": "CRITICAL",
                        "mensaje": f"El año '{row.get('year')}' no pudo ser interpretado como entero."
                    })

                # Check state consistency
                state_val = str(row.get("state", "")).strip()
                if state_val:
                    state_lower = state_val.lower()
                    state_upper = state_val.upper()
                    if state_upper not in valid_state_keys and state_lower not in valid_state_names:
                        regex_violations_mask.at[idx] = True
                        metrics["regex_violations"] += 1
                        self.alerts.append({
                            "clave": clave,
                            "campo": "state",
                            "tipo_error": "Estado inválido o no oficial",
                            "nivel": "CRITICAL",
                            "mensaje": f"El estado '{state_val}' no coincide con ningún estado de la República Mexicana."
                        })

        # 6. SLA checks: if status is "en evaluación" but "estudio" (MIA) is missing
        sla_violations_mask = pd.Series(False, index=df.index)
        for idx, row in df.iterrows():
            status = str(row.get("status", "")).lower()
            files = row.get("files_downloaded")
            if files is None:
                files = []
            elif isinstance(files, (list, tuple)):
                files = [str(f).lower() for f in files if f is not None]
            elif isinstance(files, str):
                files = [f.strip().lower() for f in files.split(",") if f.strip()]
            else:
                try:
                    if pd.isna(files):
                        files = []
                    else:
                        files = [str(files).lower()]
                except Exception:
                    files = []
                
            # If in evaluation phase but "estudio" PDF is missing
            if ("evaluac" in status or "proceso" in status) and "estudio" not in files:
                sla_violations_mask.at[idx] = True
                metrics["range_violations"] += 1
                self.alerts.append({
                    "clave": str(row.get("clave", "")),
                    "campo": "files_downloaded",
                    "tipo_error": "Advertencia de SLA",
                    "nivel": "WARNING",
                    "mensaje": "Proyecto en evaluación pero falta descargar el PDF de Estudio."
                })

        # Drop mask: remove rows with missing critical fields or format/consistency violations
        drop_mask = critical_missing_mask | regex_violations_mask
        rows_to_remove = df[drop_mask]
        metrics["rows_removed"] = len(rows_to_remove)

        cleaned_df = df[~drop_mask].reset_index(drop=True)

        # Write results to markdown report
        self._write_audit_report(name, metrics, required_cols, cleaned_df)

        # Store and save metrics to JSON report
        self.all_metrics[name] = metrics
        self._save_json_report()

        return cleaned_df, metrics

    def audit_project_evaluations(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Runs quality audits on project evaluations generated by LLM:
        1. Veredicto validation (must be in VIABLE, NO_VIABLE, CONDICIONADO, PENDIENTE).
        2. Score validation (must be between 0.0 and 1.0).
        3. Confianza_pct validation (must be between 0 and 100).
        """
        name = "Project Evaluations"
        total_rows = len(df)
        metrics = {
            "dataset_name": name,
            "total_rows": total_rows,
            "missing_values": {},
            "type_errors": 0,
            "range_violations": 0,
            "regex_violations": 0,
            "duplicate_rows": 0,
            "rows_removed": 0
        }

        if total_rows == 0:
            self._write_empty_audit(name)
            return df, metrics

        # 1. Duplicates check
        duplicates = df["clave"].duplicated().sum() if "clave" in df.columns else 0
        metrics["duplicate_rows"] = int(duplicates)

        valid_veredicto = {"VIABLE", "NO_VIABLE", "CONDICIONADO", "PENDIENTE"}
        drop_mask = pd.Series(False, index=df.index)

        for idx, row in df.iterrows():
            clave = str(row.get("clave", "")).strip().upper()
            
            # Veredicto check
            veredicto = str(row.get("veredicto", "")).strip().upper()
            if veredicto not in valid_veredicto:
                drop_mask.at[idx] = True
                metrics["type_errors"] += 1
                self.alerts.append({
                    "clave": clave,
                    "campo": "veredicto",
                    "tipo_error": "Veredicto inválido",
                    "nivel": "CRITICAL",
                    "mensaje": f"El veredicto '{veredicto}' no es reconocido. Valores admitidos: {list(valid_veredicto)}."
                })

            # Score check
            try:
                score = float(row.get("score", 0.0))
                if score < 0.0 or score > 1.0:
                    drop_mask.at[idx] = True
                    metrics["range_violations"] += 1
                    self.alerts.append({
                        "clave": clave,
                        "campo": "score",
                        "tipo_error": "Score fuera de rango",
                        "nivel": "CRITICAL",
                        "mensaje": f"El score {score} está fuera del rango admitido [0.0, 1.0]."
                    })
            except Exception:
                drop_mask.at[idx] = True
                metrics["type_errors"] += 1
                self.alerts.append({
                    "clave": clave,
                    "campo": "score",
                    "tipo_error": "Score no numérico",
                    "nivel": "CRITICAL",
                    "mensaje": f"El score '{row.get('score')}' no es un float válido."
                })

            # Confianza check
            try:
                confianza = int(row.get("confianza_pct", 0))
                if confianza < 0 or confianza > 100:
                    drop_mask.at[idx] = True
                    metrics["range_violations"] += 1
                    self.alerts.append({
                        "clave": clave,
                        "campo": "confianza_pct",
                        "tipo_error": "Confianza fuera de rango",
                        "nivel": "CRITICAL",
                        "mensaje": f"La confianza {confianza}% está fuera del rango [0, 100]."
                    })
            except Exception:
                drop_mask.at[idx] = True
                metrics["type_errors"] += 1
                self.alerts.append({
                    "clave": clave,
                    "campo": "confianza_pct",
                    "tipo_error": "Confianza no entera",
                    "nivel": "CRITICAL",
                    "mensaje": f"La confianza '{row.get('confianza_pct')}' no es un entero válido."
                })

        metrics["rows_removed"] = int(drop_mask.sum())
        cleaned_df = df[~drop_mask].reset_index(drop=True)

        # Write results to markdown report
        self._write_eval_audit_report(name, metrics, cleaned_df)

        self.all_metrics[name] = metrics
        self._save_json_report()

        return cleaned_df, metrics

    def _write_empty_audit(self, name: str):
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"### Dataset: {name}\n")
            f.write(f"⚠️ **El dataset está vacío. No se realizaron auditorías.**\n\n---\n\n")

    def _write_audit_report(self, name: str, metrics: Dict[str, Any], required_cols: List[str], cleaned_df: pd.DataFrame):
        total = metrics["total_rows"]
        removed = metrics["rows_removed"]
        remaining = len(cleaned_df)
        
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"### Dataset: {name}\n\n")
            f.write(f"| Métrica | Valor |\n")
            f.write(f"| :--- | :--- |\n")
            f.write(f"| Total de registros iniciales | `{total}` |\n")
            f.write(f"| Registros duplicados | `{metrics['duplicate_rows']}` |\n")
            f.write(f"| Violaciones de SLA (Estudios Faltantes) | `{metrics['range_violations']}` |\n")
            f.write(f"| Violaciones de Formato / Consistencia de Año / Estado | `{metrics['regex_violations']}` |\n")
            f.write(f"| Registros removidos (Formato corrupto) | `{removed}` ({(removed/total*100) if total > 0 else 0:.1f}%) |\n")
            f.write(f"| **Registros listos para ingesta** | **`{remaining}`** ({(remaining/total*100) if total > 0 else 0:.1f}%) |\n\n")

            if metrics["missing_values"]:
                f.write(f"#### Valores Nulos / Faltantes Detectados\n\n")
                f.write(f"| Columna | Cantidad Nulos | Porcentaje |\n")
                f.write(f"| :--- | :---: | :---: |\n")
                for col, info in metrics["missing_values"].items():
                    warning = " ⚠️" if col in required_cols else ""
                    f.write(f"| `{col}`{warning} | `{info['count']}` | `{info['percentage']}%` |\n")
                f.write(f"\n*(Nota: ⚠️ indica columna requerida obligatoria)*\n\n")
            else:
                f.write(f"✅ No se detectaron valores nulos en el dataset.\n\n")
                
            f.write(f"---\n\n")

    def _write_eval_audit_report(self, name: str, metrics: Dict[str, Any], cleaned_df: pd.DataFrame):
        total = metrics["total_rows"]
        removed = metrics["rows_removed"]
        remaining = len(cleaned_df)
        
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"### Dataset: {name}\n\n")
            f.write(f"| Métrica | Valor |\n")
            f.write(f"| :--- | :--- |\n")
            f.write(f"| Total de registros iniciales | `{total}` |\n")
            f.write(f"| Registros duplicados | `{metrics['duplicate_rows']}` |\n")
            f.write(f"| Errores de Tipo de Datos | `{metrics['type_errors']}` |\n")
            f.write(f"| Violaciones de Rango de Score/Confianza | `{metrics['range_violations']}` |\n")
            f.write(f"| Registros removidos (Formato corrupto) | `{removed}` ({(removed/total*100) if total > 0 else 0:.1f}%) |\n")
            f.write(f"| **Registros listos para ingesta** | **`{remaining}`** ({(remaining/total*100) if total > 0 else 0:.1f}%) |\n\n")
            f.write(f"---\n\n")
