"""
core/structured_extractor.py
Extracción Estructurada Avanzada de Impactos, Mitigaciones y Evaluación Legal con Pydantic.
Estrategia Híbrida: Local Gemma 4 E2B @ 8083 -> Gemini 2.0 Flash API Fallback.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

from core.llm_client import generate_completion, query_gemini_api

logger = logging.getLogger(__name__)


class EnvironmentalImpact(BaseModel):
    category: str = Field(description="Categoría: Suelo, Agua, Flora, Fauna, Aire, Socioeconómico, Paisaje")
    description: str = Field(description="Descripción sintética del impacto ambiental identificado")
    severity: str = Field(default="MEDIA", description="Gravedad del impacto: ALTA, MEDIA, BAJA")
    mitigation_measure: str = Field(description="Medida de prevención o mitigación correspondiente")


class ProjectEvaluation(BaseModel):
    clave: str = Field(description="Clave oficial de proyecto SEMARNAT / ASEA")
    project_name: str = Field(description="Nombre completo del proyecto")
    promovente: Optional[str] = Field(default="No especificado", description="Empresa o entidad promovente")
    summary: str = Field(description="Resumen ejecutivo del proyecto y sus alcances")
    impacts: List[EnvironmentalImpact] = Field(default_factory=list, description="Lista de impactos ambientales identificados")
    mitigations: List[str] = Field(default_factory=list, description="Lista de medidas de mitigación requeridas")
    legal_risk_level: str = Field(default="BAJO", description="Nivel de riesgo legal o condicionante: ALTO, MEDIO, BAJO")
    confidence_score: float = Field(default=0.95, description="Nivel de certeza de la extracción (0.0 a 1.0)")


class StructuredExtractor:
    def __init__(self, use_gemini_fallback: bool = True):
        self.use_gemini_fallback = use_gemini_fallback

    def extract_from_markdown(self, clave: str, md_content: str) -> ProjectEvaluation:
        """
        Extrae estructuradamente la información de un documento Markdown.
        Utiliza prioridad Gemma 4 E2B local y fallback a Gemini 2.0 Flash API.
        """
        prompt = f"""Analiza el siguiente documento ambiental y extrae la información en formato estricto JSON.

DOCUMENTO (Clave: {clave}):
```markdown
{md_content[:6000]}
```

Responde ÚNICAMENTE con una estructura JSON válida que cumpla con el siguiente esquema:
{{
  "clave": "{clave}",
  "project_name": "Nombre completo del proyecto",
  "promovente": "Nombre del promovente o empresa",
  "summary": "Resumen ejecutivo de 2 a 3 oraciones",
  "impacts": [
    {{
      "category": "Suelo/Agua/Flora/Fauna/Aire/Socioeconómico",
      "description": "Descripción del impacto",
      "severity": "ALTA/MEDIA/BAJA",
      "mitigation_measure": "Medida de mitigación propuesta"
    }}
  ],
  "mitigations": ["Medida de mitigación 1", "Medida de mitigación 2"],
  "legal_risk_level": "ALTO/MEDIO/BAJO",
  "confidence_score": 0.95
}}
"""

        # 1. Intentar con Inferencia Local (Gemma 4 E2B @ 8083)
        raw_response = ""
        try:
            res_dict = generate_completion(prompt, response_json=True)
            if isinstance(res_dict, dict):
                raw_response = json.dumps(res_dict)
            else:
                raw_response = str(res_dict)
        except Exception as exc:
            logger.warning("Fallo en inferencia local para %s: %s", clave, exc)


        parsed_eval = self._parse_json_to_evaluation(clave, raw_response)
        if parsed_eval:
            return parsed_eval

        # 2. Fallback a Gemini 2.0 Flash API Cloud si se requiere
        if self.use_gemini_fallback:
            logger.info("Ejecutando fallback a Gemini 2.0 Flash API para clave %s...", clave)
            gemini_resp = query_gemini_api(prompt)
            if gemini_resp and not gemini_resp.startswith("[LLM Error]"):
                parsed_gemini = self._parse_json_to_evaluation(clave, gemini_resp)
                if parsed_gemini:
                    return parsed_gemini

        # 3. Fallback Heurístico Estructurado por Defecto
        return self._build_fallback_evaluation(clave, md_content)

    def _parse_json_to_evaluation(self, clave: str, raw_text: str) -> Optional[ProjectEvaluation]:
        if not raw_text:
            return None

        # Limpiar bloques markdown ```json ... ```
        clean_json = raw_text.strip()
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean_json, re.DOTALL)
        if match:
            clean_json = match.group(1)
        else:
            match_obj = re.search(r"(\{.*\})", clean_json, re.DOTALL)
            if match_obj:
                clean_json = match_obj.group(1)

        try:
            data = json.loads(clean_json)
            data["clave"] = clave
            return ProjectEvaluation(**data)
        except Exception as exc:
            logger.debug("Error parseando JSON LLM para %s: %s", clave, exc)
            return None

    def _build_fallback_evaluation(self, clave: str, md_content: str) -> ProjectEvaluation:
        title = clave
        lines = md_content.splitlines()
        for l in lines[:10]:
            if l.startswith("# "):
                title = l.replace("# ", "").strip()
                break

        impacts = [
            EnvironmentalImpact(
                category="Suelo/Flora",
                description="Afección potencial a cobertura vegetal por preparación del sitio",
                severity="MEDIA",
                mitigation_measure="Programa de rescate de flora y reforestación compensatoria"
            )
        ]

        return ProjectEvaluation(
            clave=clave,
            project_name=title if title != clave else f"Proyecto {clave}",
            promovente="Información no extractable",
            summary=md_content[:300].replace("\n", " ").strip() if md_content else "Sin resumen disponible.",
            impacts=impacts,
            mitigations=["Programa de monitoreo ambiental", "Delimitación del área de trabajo"],
            legal_risk_level="MEDIO",
            confidence_score=0.70
        )
