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
        # Create/Clear report on init
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(self.report_path, "w", encoding="utf-8") as f:
            f.write(f"# Reporte de Auditoría de Calidad de Datos (SEMARNAT)\n")
            f.write(f"Generado el: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        # Clear JSON report on init
        with open(self.json_report_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

    def log_section(self, title: str):
        with open(self.report_path, "a", encoding="utf-8") as f:
            f.write(f"## {title}\n\n")

    def audit_semarnat_projects(
        self, 
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Runs specific quality audits on SEMARNAT projects:
        1. Clave format validation (regex).
        2. Year consistency (clave year matching metadata year).
        3. Missing required fields (clave, project_name, status).
        4. SLA check (warning if status is 'En evaluación' but MIA study is missing).
        """
        name = "SEMARNAT Projects"
        total_rows = len(df)
        metrics = {
            "dataset_name": name,
            "total_rows": total_rows,
            "missing_values": {},
            "type_errors": {},
            "range_violations": 0,  # Used for SLA violations
            "regex_violations": 0,  # Used for Clave & Year format errors
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
        
        for idx, row in df.iterrows():
            clave = str(row.get("clave", "")).strip().upper()
            if not clave_pattern.match(clave):
                regex_violations_mask.at[idx] = True
                metrics["regex_violations"] += 1
            else:
                # 5. Year consistency check
                clave_year = clave[4:8]
                row_year = str(row.get("year", ""))
                if clave_year not in row_year:
                    regex_violations_mask.at[idx] = True
                    metrics["regex_violations"] += 1

        # 6. SLA checks: if status is "en evaluación" but "estudio" (MIA) is missing
        sla_violations_mask = pd.Series(False, index=df.index)
        for idx, row in df.iterrows():
            status = str(row.get("status", "")).lower()
            files = row.get("files_downloaded", [])
            # Convert files to list if string representation
            if isinstance(files, str):
                files = [f.strip().lower() for f in files.split(",") if f.strip()]
            else:
                files = [str(f).lower() for f in files]
                
            # If in evaluation phase but "estudio" PDF is missing
            if ("evaluac" in status or "proceso" in status) and "estudio" not in files:
                sla_violations_mask.at[idx] = True
                metrics["range_violations"] += 1

        # Drop mask: remove rows with missing critical fields or format/consistency violations
        # We flag SLA violations as warnings but DO NOT discard the row, so we keep them in the warehouse
        # (This is standard best practice: discard corrupt format rows, but keep rows with SLA warnings for audit).
        drop_mask = critical_missing_mask | regex_violations_mask
        rows_to_remove = df[drop_mask]
        metrics["rows_removed"] = len(rows_to_remove)

        cleaned_df = df[~drop_mask].reset_index(drop=True)

        # Write results to markdown report
        self._write_audit_report(name, metrics, required_cols, cleaned_df)

        # Store and save metrics to JSON report
        self.all_metrics[name] = metrics
        with open(self.json_report_path, "w", encoding="utf-8") as f:
            json.dump(self.all_metrics, f, indent=2)

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
            f.write(f"| Violaciones de Formato / Consistencia de Año | `{metrics['regex_violations']}` |\n")
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
