"""
core/second_brain.py
Motor de generación de la base de conocimiento "Second Brain" de la Base de Conocimiento de Zohar.
Procesa archivos descargados, gacetas, extracciones e inferencias.
"""

from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# Reutilizar el regex y mapeos de graph_builder
from core.graph_builder import parse_semarnat_key, ESTADO_NOMBRES, TIPO_MIA

class SecondBrainBuilder:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.downloads_dir = self.base_dir / "downloads"
        self.extractions_dir = self.base_dir / "extractions"
        self.data_dir = self.base_dir / "data"
        self.inference_cache_dir = self.data_dir / "inference_cache"
        self.sb_dir = self.base_dir / "second_brain"

        self.clave_re = re.compile(r"(?<![A-Z0-9])(\d{2}[A-Z]{2}\d{4}[A-Z0-9]\d{3,5})(?![A-Z0-9])")

    def build_vault(self) -> dict:
        """
        Construye la bóveda completa de la Base de Conocimiento de Zohar en second_brain/.
        Retorna estadísticas de la generación.
        """
        # 1. Asegurar directorios de salida
        self.sb_dir.mkdir(parents=True, exist_ok=True)
        sources_dir = self.sb_dir / "01_Sources"
        entities_dir = self.sb_dir / "02_Entities"
        inferences_dir = self.sb_dir / "03_Inferences"

        for d in [sources_dir, entities_dir, inferences_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 2. Escanear todo el corpus
        pdfs = self._scan_pdfs()
        extractions = self._scan_extractions()
        inferences = self._scan_inferences()

        # 3. Indexar proyectos
        projects = self._index_projects(pdfs, extractions, inferences)

        # 4. Escanear gacetas y asociar proyectos
        gacetas = self._process_gacetas(pdfs, extractions, projects)

        # 5. Generar notas de Gacetas
        for gaceta_name, gaceta_info in gacetas.items():
            self._write_gaceta_note(sources_dir, gaceta_name, gaceta_info)

        # 6. Generar notas de Proyectos, Extracciones e Inferencias
        municipios = {}
        sectores = {}
        tipos = {}

        for clave, proj in projects.items():
            # Generar nota del proyecto
            self._write_project_note(entities_dir, clave, proj)

            # Agrupar por entidad para las notas colectoras
            if proj.get("valid"):
                muni_name = proj.get("estado_nombre")
                if muni_name:
                    municipios.setdefault(muni_name, []).append(clave)

                sect_id = proj.get("sector")
                if sect_id:
                    sectores.setdefault(sect_id, []).append(clave)

                tipo_name = proj.get("tipo_nombre")
                if tipo_name:
                    tipos.setdefault(tipo_name, []).append(clave)

            # Generar nota de inferencia si existe
            if proj.get("inference"):
                self._write_inference_note(inferences_dir, clave, proj["inference"])

        # 7. Generar notas agrupadoras (Entidades de segundo nivel)
        for muni_name, p_list in municipios.items():
            self._write_collector_note(entities_dir, f"Municipio - {muni_name}", "Municipio/Estado", p_list)

        for sect_id, p_list in sectores.items():
            self._write_collector_note(entities_dir, f"Sector - {sect_id}", "Sector de Impacto", p_list)

        for tipo_name, p_list in tipos.items():
            self._write_collector_note(entities_dir, f"Tipo - {tipo_name}", "Tipo de MIA", p_list)

        # 8. Generar 00_Index.md y 00_Workflow.md
        self._write_index_note(projects, gacetas, pdfs)
        self._write_workflow_note()

        return {
            "total_proyectos": len(projects),
            "total_gacetas": len(gacetas),
            "total_municipios": len(municipios),
            "total_inferencias": sum(1 for p in projects.values() if p.get("inference")),
        }

    def _scan_pdfs(self) -> dict[str, list[dict]]:
        """Busca y agrupa archivos PDF en downloads/."""
        pdf_groups = {}
        if not self.downloads_dir.exists():
            return pdf_groups

        for pdf in self.downloads_dir.rglob("*.pdf"):
            folder = pdf.parent.name
            stat = pdf.stat()
            pdf_info = {
                "name": pdf.name,
                "path": pdf,
                "size_bytes": stat.st_size,
                "modified_ts": stat.st_mtime,
            }
            pdf_groups.setdefault(folder, []).append(pdf_info)
        return pdf_groups

    def _scan_extractions(self) -> dict[str, dict]:
        """Escanea la carpeta extractions/ buscando archivos .md."""
        md_files = {}
        if not self.extractions_dir.exists():
            return md_files

        for md in self.extractions_dir.glob("*.md"):
            md_files[md.stem] = {
                "name": md.name,
                "path": md,
                "modified_ts": md.stat().st_mtime,
            }
        return md_files

    def _scan_inferences(self) -> dict[str, dict]:
        """Escanea la caché de reportes de inferencia en data/inference_cache/."""
        inf_reports = {}
        if not self.inference_cache_dir.exists():
            return inf_reports

        for js in self.inference_cache_dir.glob("*.json"):
            try:
                data = json.loads(js.read_text(encoding="utf-8", errors="ignore"))
                inf_reports[js.stem] = data
            except Exception as exc:
                logger.warning("Error leyendo reporte inferencia %s: %s", js.name, exc)
        return inf_reports

    def _index_projects(self, pdfs: dict, extractions: dict, inferences: dict) -> dict:
        """Asocia todos los recursos de un proyecto en un solo mapa indexado."""
        projects = {}

        # 1. Recopilar desde los PDFs de estudios, resumenes, resolutivos
        for category in ["estudios", "resumenes", "resolutivos"]:
            for pdf in pdfs.get(category, []):
                parsed = parse_semarnat_key(pdf["name"])
                clave = parsed["clave"]
                
                projects.setdefault(clave, {
                    "clave": clave,
                    "valid": parsed.get("valid", False),
                    "sector": parsed.get("sector"),
                    "estado": parsed.get("estado"),
                    "estado_nombre": parsed.get("estado_nombre"),
                    "year": parsed.get("year"),
                    "tipo": parsed.get("tipo"),
                    "tipo_nombre": parsed.get("tipo_nombre"),
                    "estudio_pdf": None,
                    "resumen_pdf": None,
                    "resolutivo_pdf": None,
                    "extraction": None,
                    "inference": None,
                })

                projects[clave][f"{category[:-1]}_pdf"] = pdf

        # 2. Asociar extractions
        for name, md in extractions.items():
            # Si el extraction no es una gaceta (las gacetas no tienen clave SINAT de 12-14 chars)
            parsed = parse_semarnat_key(name + ".pdf")
            if parsed.get("valid"):
                clave = parsed["clave"]
                projects.setdefault(clave, {
                    "clave": clave,
                    "valid": True,
                    "sector": parsed.get("sector"),
                    "estado": parsed.get("estado"),
                    "estado_nombre": parsed.get("estado_nombre"),
                    "year": parsed.get("year"),
                    "tipo": parsed.get("tipo"),
                    "tipo_nombre": parsed.get("tipo_nombre"),
                    "estudio_pdf": None,
                    "resumen_pdf": None,
                    "resolutivo_pdf": None,
                    "extraction": None,
                    "inference": None,
                })
                projects[clave]["extraction"] = md

        # 3. Asociar inferencias
        for name, report in inferences.items():
            parsed = parse_semarnat_key(name + ".pdf")
            if parsed.get("valid"):
                clave = parsed["clave"]
                if clave in projects:
                    projects[clave]["inference"] = report

        return projects

    def _process_gacetas(self, pdfs: dict, extractions: dict, projects: dict) -> dict:
        """Encuentra gacetas y busca qué proyectos están asociados a ellas."""
        gacetas = {}

        # 1. Indexar gacetas en PDFs (tanto de SINAT en "gacetas" como de ASEA en "asea")
        for pdf in pdfs.get("gacetas", []):
            stem = Path(pdf["name"]).stem
            gacetas[stem] = {
                "pdf": pdf,
                "extraction": extractions.get(stem),
                "proyectos": [],
            }

        for pdf in pdfs.get("asea", []):
            stem = Path(pdf["name"]).stem
            gacetas[stem] = {
                "pdf": pdf,
                "extraction": extractions.get(stem),
                "proyectos": [],
            }

        # 2. Si no hay PDFs pero hay extractions de gacetas o archivos de la ASEA
        for name, md in extractions.items():
            if ("gaceta" in name.lower() or name.startswith("ASEA_")) and name not in gacetas:
                gacetas[name] = {
                    "pdf": None,
                    "extraction": md,
                    "proyectos": [],
                }

        # 3. Escanear textos extraídos de las gacetas para vincular proyectos (relación bidireccional)
        for name, info in gacetas.items():
            if info["extraction"]:
                try:
                    content = info["extraction"]["path"].read_text(encoding="utf-8", errors="ignore")
                    # Extraer claves SINAT usando regex
                    found_claves = set(self.clave_re.findall(content.upper()))
                    for c in found_claves:
                        # Crear entrada stub si el proyecto aún no tiene archivos indexados
                        if c not in projects:
                            from core.graph_builder import parse_semarnat_key
                            parsed = parse_semarnat_key(c + ".pdf")
                            projects[c] = {
                                "clave": c,
                                "valid": parsed.get("valid", False),
                                "sector": parsed.get("sector"),
                                "estado": parsed.get("estado"),
                                "estado_nombre": parsed.get("estado_nombre"),
                                "year": parsed.get("year"),
                                "tipo": parsed.get("tipo"),
                                "tipo_nombre": parsed.get("tipo_nombre"),
                                "estudio_pdf": None,
                                "resumen_pdf": None,
                                "resolutivo_pdf": None,
                                "extraction": None,
                                "inference": None,
                            }
                        info["proyectos"].append(c)
                        # También asociamos la gaceta al proyecto
                        projects[c]["gaceta_origen"] = name
                except Exception as exc:
                    logger.error("Error analizando contenido de gaceta %s: %s", name, exc)

        return gacetas


    # ===========================================================================
    # Escritura de Notas Markdown (Zohar KB Templates)
    # ===========================================================================

    def _write_gaceta_note(self, dest_dir: Path, name: str, info: dict):
        """Escribe una nota para una gaceta."""
        is_asea = name.upper().startswith("ASEA_")
        prefix = "Gaceta ASEA" if is_asea else "Gaceta"
        note_path = dest_dir / f"{prefix} - {name}.md"
        pdf_link = f"[{info['pdf']['name']}](file://{info['pdf']['path']})" if info.get("pdf") else "No disponible"
        md_link = f"[{info['extraction']['name']}](file://{info['extraction']['path']})" if info.get("extraction") else "No extraído"

        proyectos_section = ""
        if info["proyectos"]:
            proyectos_section = "\n".join(f"- [[Proyecto - {c}]]" for c in sorted(info["proyectos"]))
        else:
            proyectos_section = "_No se detectaron proyectos vinculados en esta gaceta o no han sido indexados._"

        content = f"""---
type: source
category: gaceta
name: {name}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# {"Gaceta Ecológica ASEA" if is_asea else "Gaceta Ecológica"}: {name}

## [ARCHIVOS] Relacionados
- **PDF Original:** {pdf_link}
- **Texto Extraído (.md):** {md_link}

---

## [PROYECTOS] Anunciados / Vinculados
Esta gaceta anuncia los siguientes proyectos ecológicos evaluados:

{proyectos_section}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_project_note(self, dest_dir: Path, clave: str, proj: dict):
        """Escribe una nota estructurada para un proyecto (clave SINAT)."""
        note_path = dest_dir / f"Proyecto - {clave}.md"

        metadata_sec = ""
        if proj.get("valid"):
            metadata_sec = f"""## [FICHA] Técnica
- **Clave de Proyecto:** {clave}
- **Estado/Ubicación:** [[Municipio - {proj['estado_nombre']}]]
- **Año de Registro:** {proj['year']}
- **Sector Productivo:** [[Sector - {proj['sector']}]]
- **Tipo de Trámite:** [[Tipo - {proj['tipo_nombre']}]]"""
        else:
            metadata_sec = f"""## [!] Ficha Técnica (Formato Especial)
- **Clave Identificada:** {clave}
- _Nota: Esta clave no cumple el formato estándar de SEMARNAT de 12-14 caracteres._"""

        # Enlace a Gaceta
        gaceta_sec = ""
        if proj.get("gaceta_origen"):
            orig = proj['gaceta_origen']
            is_asea = orig.upper().startswith("ASEA_")
            prefix = "Gaceta ASEA" if is_asea else "Gaceta"
            gaceta_sec = f"- **Gaceta de Anuncio:** [[{prefix} - {orig}]]"
        else:
            gaceta_sec = "- **Gaceta de Anuncio:** _No detectada en el corpus local._"

        # Archivos PDF
        files_sec = ""
        for cat in ["estudio", "resumen", "resolutivo"]:
            field = f"{cat}_pdf"
            if proj.get(field):
                files_sec += f"- **PDF de {cat.capitalize()}:** [{proj[field]['name']}](file://{proj[field]['path']})\n"
            else:
                files_sec += f"- **PDF de {cat.capitalize()}:** _No descargado_\n"

        # Extracción e inferencia
        ext_sec = ""
        if proj.get("extraction"):
            ext_sec = f"- **Texto Markdown Extraído:** [{proj['extraction']['name']}](file://{proj['extraction']['path']})"
        else:
            ext_sec = "- **Texto Markdown Extraído:** _No procesado_"

        inf_sec = ""
        if proj.get("inference"):
            inf_sec = f"- **Reporte de Dictamen:** [[Inferencia - {clave}]] (Veredicto: **{proj['inference'].get('veredicto', 'SIN EVALUAR')}**)"
        else:
            inf_sec = f"- **Reporte de Dictamen:** _Inferencia no ejecutada. Lanza el motor de evaluación para este proyecto._"

        content = f"""---
type: entity
category: proyecto
clave: {clave}
valid: {proj.get('valid', False)}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Proyecto SEMARNAT: {clave}

{metadata_sec}

---

## [ARCHIVOS] Documentos del Trámite
{files_sec}
{ext_sec}

---

## [DICTAMEN] Inteligencia Jurídica & Dictamen
{gaceta_sec}
{inf_sec}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_inference_note(self, dest_dir: Path, clave: str, report: dict):
        """Escribe una nota detallada del reporte de inferencia de Gemini."""
        note_path = dest_dir / f"Inferencia - {clave}.md"

        veredicto = report.get("veredicto", "SINDICTAMEN")
        score = report.get("score", 0.0)
        confianza = report.get("confianza_pct", 0)

        # Formatear señales a favor y en contra
        yes_signals = "\n".join(f"- {s}" for s in report.get("yes_signals", [])) or "_Sin señales registradas_"
        no_signals = "\n".join(f"- {s}" for s in report.get("no_signals", [])) or "_Sin señales de rechazo registradas_"
        knockouts = "\n".join(f"- [X] **{s}**" for s in report.get("knockouts", [])) or "_Ningún knockout activado_"
        condicionantes = "\n".join(f"- [*] {s}" for s in report.get("condicionantes", [])) or "_Sin condicionantes especificadas_"

        meta_mod = report.get("meta", {}).get("modelo", "Desconocido")

        content = f"""---
type: inference
category: dictamen
clave: {clave}
veredicto: {veredicto}
score: {score}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Dictamen de Inferencia: {clave}
Asociado al proyecto: [[Proyecto - {clave}]]

---

## [DICTAMEN] Veredicto: **{veredicto}**
- **Viabilidad Socio-Ambiental (Score):** `{score * 100}%`
- **Confianza de la Evaluación:** `{confianza}%`
- **Modelo de Evaluación:** `{meta_mod}`

---

## [X] Filtros Fatales (Knockouts Detectados)
Si se encuentra algún knockout, la viabilidad se reduce a 0 de forma automática:
{knockouts}

---

## [+] Señales de Viabilidad (A Favor)
{yes_signals}

---

## [-] Riesgos e Impactos Negativos (En Contra)
{no_signals}

---

## [*] Medidas de Mitigación Requeridas (Condicionantes)
{condicionantes}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_collector_note(self, dest_dir: Path, name: str, type_label: str, projects_list: list[str]):
        """Escribe una nota colectora que agrupa proyectos (ej. por Municipio o Sector)."""
        note_path = dest_dir / f"{name}.md"

        proyectos_section = "\n".join(f"- [[Proyecto - {c}]]" for c in sorted(projects_list))

        content = f"""---
type: collector
category: {type_label.lower().replace('/', '_')}
name: {name}
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# {type_label}: {name.split(" - ")[-1]}

## [ARCHIVOS] Proyectos Relacionados
Se registran `{len(projects_list)}` proyectos vinculados a esta categoría en el corpus local:

{proyectos_section}
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_index_note(self, projects: dict, gacetas: dict, pdfs: dict):
        """Escribe el archivo 00_Index.md raíz de la bóveda."""
        note_path = self.sb_dir / "00_Index.md"

        total_estudios = sum(1 for p in projects.values() if p.get("estudio_pdf"))
        total_resolutivos = sum(1 for p in projects.values() if p.get("resolutivo_pdf"))
        total_inferencias = sum(1 for p in projects.values() if p.get("inference"))

        # Lista de gacetas
        gacetas_lines = []
        for g, info in sorted(gacetas.items()):
            is_asea = g.upper().startswith("ASEA_")
            prefix = "Gaceta ASEA" if is_asea else "Gaceta"
            gacetas_lines.append(f"- [[{prefix} - {g}]] (`{len(info['proyectos'])}` proyectos)")
        gacetas_sec = "\n".join(gacetas_lines) or "_Ninguna gaceta indexada_"

        # Lista de proyectos recientes (últimos 15)
        recientes_sec = "\n".join(f"- [[Proyecto - {c}]] (Ubicación: {p.get('estado_nombre', 'Desconocida')} | Dictamen: **{p['inference'].get('veredicto', 'SIN EVALUAR') if p.get('inference') else 'PENDIENTE'}**)" for c, p in sorted(projects.items(), key=lambda x: x[0], reverse=True)[:15]) or "_Ningún proyecto indexado_"

        content = f"""---
type: index
name: Zohar Second Brain
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Zohar Intelligence v4 — Second Brain

Bienvenido a la Bóveda de Conocimiento de Proyectos Ambientales SEMARNAT/ASEA. Este espacio está estructurado con enlaces bidireccionales y notas Markdown listas para ser navegadas en el dashboard de Zohar v4 de manera local.

---

## Workflow del Sistema
Para comprender el pipeline completo de adquisición y procesamiento de datos, consulta la guía detallada:
- [[00_Workflow|Explicación del Workflow del Sistema y Flujo de Información]]

---

## Estadísticas del Corpus
- Total de Gacetas Indexadas: `{len(gacetas)}`
- Total de Proyectos Identificados: `{len(projects)}`
- Estudios de Impacto Ambiental (MIA): `{total_estudios}`
- Resolutivos Oficiales: `{total_resolutivos}`
- Evaluaciones de Inferencia (IA): `{total_inferencias}`

---

## Gacetas Ecológicas
Navega las gacetas y los proyectos anunciados en ellas:
{gacetas_sec}

---

## Proyectos Indexados Recientemente
{recientes_sec}

---

## Directorios de la Bóveda
- [[01_Sources/|Fuentes Crudas (PDFs y Conversiones)]]
- [[02_Entities/|Entidades (Proyectos, Municipios, Sectores y Tipos)]]
- [[03_Inferences/|Dictámenes de Inferencia (Filtros y Mitigación)]]
"""
        note_path.write_text(content, encoding="utf-8")

    def _write_workflow_note(self):
        """Escribe el archivo 00_Workflow.md explicando el funcionamiento del pipeline."""
        note_path = self.sb_dir / "00_Workflow.md"

        content = f"""---
type: index
name: Zohar Workflow
date_generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
---

# Workflow del Sistema Zohar Intelligence v4

Esta nota detalla el flujo de información del sistema, desde la ingesta de las gacetas oficiales de SEMARNAT/ASEA hasta el dictamen de inferencia y la exportación de conocimiento.

---

## Fase 1: Adquisición de Gacetas (Scraping)
El pipeline inicia peinando y monitoreando los portales de publicaciones oficiales de gacetas ecológicas:
- **SEMARNAT (SINAT):** Utiliza un motor de navegación Selenium (`GazetteScraper`) que entra al portal de trámites, interactúa con los iframes de descarga y adquiere los archivos de las gacetas del año configurado.
- **ASEA:** Scraper directo basado en HTTP requests (`ASEAScraper`) que consulta y descarga las gacetas disponibles.
- Los archivos resultantes se guardan de forma organizada en `downloads/gacetas/`.

---

## Fase 2: Extracción de Claves SINAT
Una vez descargada una gaceta:
- Se convierte a texto Markdown (.md) mediante un extractor de texto.
- Se ejecuta una búsqueda por expresiones regulares buscando claves del formato de trámite de SEMARNAT (SINAT): `XX[ESTADO]YYYY[TIPO][SECUENCIA]` (ejemplo: `21PU2025H0155`).
- El backend asocia estas claves con su gaceta de origen en el log del sistema.

---

## Fase 3: Descarga de Estudios y Resolutivos
Con las claves SINAT identificadas en las gacetas:
- El descargador de SEMARNAT interactúa dinámicamente con el portal de consulta de trámites ingresando la clave.
- Detecta los botones de descarga correspondientes a:
  - Resumen del Proyecto.
  - Estudio de Impacto Ambiental (MIA).
  - Resolutivo Oficial de Dictamen.
- Los PDFs resultantes se descargan y clasifican mediante un clasificador posicional heurístico en `downloads/resumenes/`, `downloads/estudios/` y `downloads/resolutivos/`.

---

## Fase 4: Conversión a Markdown (MD_LAB)
Para poder realizar análisis semánticos y de inferencia jurídica:
- Se procesan los archivos PDF a través del motor `pdf_processor.py` (usando `pymupdf4llm`).
- Genera archivos Markdown limpios estructurados por bloques, tablas y secciones en `extractions/`.
- Este proceso cuenta con una estrategia de caché reactiva basada en marcas de tiempo (`mtime`) para evitar reprocesamientos costosos.

---

## Fase 5: Inferencia Socio-Ambiental (INFERENCE_LAB)
El motor de inferencia (`inference_engine.py`):
- Evalúa el texto Markdown de los estudios utilizando la API de Gemini (o fallbacks locales).
- Busca activamente señales de aprobación (Yes Signals), riesgos socio-ambientales (No Signals), traslapes de coordenadas (WKT), especies protegidas (NOM-059) y knockouts de rechazo inmediatos.
- El resultado se compila en un dictamen estructurado en formato JSON y se almacena en `data/inference_cache/`.

---

## Fase 6: Consolidación del Grafo y Second Brain
Como paso de cierre:
- **Grafo D3:** Genera un grafo de relaciones de red para explorar geográficamente los proyectos por estado, sector y año.
- **Second Brain:** Se estructuran de forma automática todos los archivos en el directorio `second_brain/`, generando un repositorio de notas interconectadas vinculadas bidireccionalmente mediante enlaces `[[Wiki-Link]]` para la base de datos de Zohar.
"""
        note_path.write_text(content, encoding="utf-8")

