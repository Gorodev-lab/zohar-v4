"""
core/subagents/graph_extractor.py
=================================
Sub-agente especializado para la extracción de entidades y relaciones
(grafos de conocimiento) a partir de documentos ambientales de SEMARNAT.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Any, Optional
from core.rlm_harness import RLMHarness
from core.llm_client import generate_completion

logger = logging.getLogger(__name__)


class GraphExtractor:
    """
    Sub-agente para la extracción de Grafos de Conocimiento usando RLMHarness.
    """

    def __init__(self, harness: RLMHarness):
        """
        Inicializa el extractor de grafos.

        Args:
            harness: Instancia de RLMHarness de la que obtener el texto de los documentos.
        """
        self.harness = harness

    def extract_graph(self, doc_id: str) -> Dict[str, Any]:
        """
        Analiza el texto de un documento referenciado por doc_id, extrae
        nodos y relaciones y los devuelve bajo un formato JSON estructurado.

        Args:
            doc_id: Identificador simbólico (ej. '[VAR_DOC_01]') en el RLMHarness.

        Returns:
            Dict con claves "nodes" y "relations".
        """
        logger.info(f"GraphExtractor: Iniciando extracción de grafo para {doc_id}")
        raw_text = self.harness.get_offloaded_text(doc_id)
        if not raw_text:
            err_msg = f"No se encontró el documento {doc_id} en el almacén de RLM."
            logger.error(err_msg)
            return {"status": "ERROR", "message": err_msg, "nodes": [], "relations": []}

        # Truncado / chunking básico para evitar exceder límites de contexto local si es extremadamente masivo
        max_chars_to_analyze = 6000
        analysis_snippet = raw_text
        if len(raw_text) > max_chars_to_analyze:
            logger.warning(f"GraphExtractor: Texto del documento {doc_id} muy largo ({len(raw_text)} chars). Limitando a los primeros {max_chars_to_analyze} caracteres para análisis.")
            analysis_snippet = raw_text[:max_chars_to_analyze] + "\n\n[...TEXTO TRUNCADO EN EXTRACCIÓN...]"

        # Definir la tarea y el esquema esperado
        task = "Analizar el texto provisto y extraer entidades clave y sus relaciones en un grafo de conocimiento."
        
        expected_schema = {
            "nodes": [
                {
                    "id": "Identificador único en MAYÚSCULAS y normalizado (ej. ACME_SA_DE_CV)",
                    "label": "Nombre descriptivo de la entidad (ej. ACME S.A. de C.V.)",
                    "type": "Tipo de entidad sugerido: 'proyecto', 'promovente', 'estado', 'municipio', 'especie', 'regulacion' (u otro tipo relevante descubierto)"
                }
            ],
            "relations": [
                {
                    "src": "ID del nodo origen",
                    "tgt": "ID del nodo destino",
                    "rel": "Relación descriptiva sugerida: 'UBICADO_EN', 'AFECTA', 'PRESENTA', 'PROTEGE', 'SANCIONA' (u otra relación relevante descubierta)"
                }
            ]
        }

        constraints = (
            "Responder estrictamente en formato JSON que coincida con expected_output_schema. "
            "Extrae únicamente entidades reales presentes en el texto. "
            "El ID del nodo origen y destino en las relaciones debe coincidir exactamente con el ID de algún nodo en la lista de 'nodes'."
        )

        # Usamos el harness para formatear el prompt bajo LID
        # Para que el LLM tenga el fragmento de texto a analizar sin romper el esquema LID,
        # lo inyectamos dentro del campo variables junto con el doc_id.
        variables = {
            "document_id": doc_id,
            "text_to_analyze": analysis_snippet
        }

        logger.info(f"GraphExtractor: Enviando prompt LID al LLM")
        
        try:
            response = self.harness.execute_lid_completion(
                task=task,
                variables=variables,
                constraints=constraints,
                expected_output_schema=expected_schema,
                system_prompt="Eres un extractor de grafos de conocimiento experto en la normativa ambiental de SEMARNAT de México."
            )

            # Si cae en fallback o error heurístico
            if response.get("is_fallback") or response.get("status") == "error":
                logger.warning("GraphExtractor: LLM falló o devolvió fallback. Generando extracción heurística básica.")
                return self._generate_heuristic_graph(analysis_snippet)

            # Normalizar claves
            nodes = response.get("nodes", [])
            relations = response.get("relations", [])

            # Limpieza básica de IDs
            for node in nodes:
                if "id" in node:
                    node["id"] = str(node["id"]).strip().upper().replace(" ", "_")

            for rel in relations:
                if "src" in rel:
                    rel["src"] = str(rel["src"]).strip().upper().replace(" ", "_")
                if "tgt" in rel:
                    rel["tgt"] = str(rel["tgt"]).strip().upper().replace(" ", "_")

            return {
                "status": "SUCCESS",
                "nodes": nodes,
                "relations": relations
            }

        except Exception as e:
            logger.error(f"GraphExtractor: Excepción durante la extracción: {e}")
            return self._generate_heuristic_graph(analysis_snippet)

    def _generate_heuristic_graph(self, text: str) -> Dict[str, Any]:
        """Genera un grafo heurístico básico si el LLM falla."""
        # Intenta buscar patrones simples en el texto (ej. Claves de proyectos, estados comunes, etc.)
        nodes = []
        relations = []

        # Buscar posible clave SEMARNAT
        import re
        from core.graph_builder import _CLAVE_RE
        
        # Buscar palabras en mayúsculas que parezcan claves
        potential_keys = re.findall(r"\b\d{2}[A-Z]{2}\d{4}[A-Z]\d{4}\b", text)
        if potential_keys:
            key = potential_keys[0]
            nodes.append({
                "id": key.upper(),
                "label": f"Proyecto {key}",
                "type": "proyecto"
            })
            # Intentar deducir estado de la clave
            state_code = key[2:4]
            from core.graph_builder import ESTADO_NOMBRES
            if state_code in ESTADO_NOMBRES:
                state_id = f"ESTADO_{state_code}"
                nodes.append({
                    "id": state_id,
                    "label": ESTADO_NOMBRES[state_code],
                    "type": "estado"
                })
                relations.append({
                    "src": key.upper(),
                    "tgt": state_id,
                    "rel": "UBICADO_EN"
                })

        # Buscar promoventes genéricos (ej. "empresa", "S.A. de C.V.")
        promovente_match = re.search(r"([A-Z][A-Za-z0-9\s,\.]+ S\.A\.(?: de C\.V\.)?)", text)
        if promovente_match:
            name = promovente_match.group(1).strip()
            name_id = name.upper().replace(" ", "_").replace(".", "")
            nodes.append({
                "id": name_id,
                "label": name,
                "type": "promovente"
            })
            if potential_keys:
                relations.append({
                    "src": potential_keys[0].upper(),
                    "tgt": name_id,
                    "rel": "PRESENTADO_POR"
                })

        return {
            "status": "HEURISTIC_FALLBACK",
            "nodes": nodes,
            "relations": relations
        }

from sqlalchemy import create_engine, text
from core.dw_pipeline import DB_URL

async def persist_graph_to_db(graph_data: dict, db_pool=None) -> dict:
    """Persiste los nodos y relaciones extraídos en PostgreSQL usando SQLAlchemy."""
    if graph_data.get("status") not in ["SUCCESS", "HEURISTIC_FALLBACK"]:
        return {"status": "SKIPPED", "reason": "No hay datos de grafo válidos para persistir."}

    try:
        engine = create_engine(DB_URL)
        with engine.begin() as conn:
            for node in graph_data.get("nodes", []):
                conn.execute(text("""
                    INSERT INTO public.kg_nodes (id, label, type)
                    VALUES (:id, :label, :type)
                    ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label, type = EXCLUDED.type
                """), {"id": str(node.get("id")), "label": str(node.get("label")), "type": str(node.get("type"))})
            
            for rel in graph_data.get("relations", []):
                conn.execute(text("""
                    INSERT INTO public.kg_edges (source, target, relationship)
                    VALUES (:src, :tgt, :rel)
                    ON CONFLICT (source, target, relationship) DO UPDATE SET weight = public.kg_edges.weight + 0.1
                """), {"src": str(rel.get("src")), "tgt": str(rel.get("tgt")), "rel": str(rel.get("rel"))})
                    
        return {"status": "PERSISTED", "nodes": len(graph_data.get("nodes", [])), "edges": len(graph_data.get("relations", []))}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error persistiendo grafo en BD: {e}")
        return {"status": "ERROR", "error": str(e)}
