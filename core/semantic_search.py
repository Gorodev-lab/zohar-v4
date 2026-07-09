"""
core/semantic_search.py
Motor de búsqueda semántica para las notas del Second Brain utilizando la API de Gemini y caché local.
"""

from __future__ import annotations

import json
import logging
import math
import os
import hashlib
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _compute_sha256(text: str) -> str:
    """Calcula el hash SHA256 de un texto."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Calcula la similitud de coseno entre dos vectores."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)

class SemanticSearchEngine:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.sb_dir = self.base_dir / "second_brain"
        self.cache_file = self.sb_dir / "embeddings_cache.json"
        
        # Modelo para embeddings
        self.embedding_model = "text-embedding-004"
        
        # Inicializar caché
        self.cache: dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self):
        """Carga la caché de embeddings desde el disco."""
        if self.cache_file.exists():
            try:
                self.cache = json.loads(self.cache_file.read_text(encoding="utf-8", errors="ignore"))
                logger.info("Caché de embeddings cargada correctamente: %d notas indexadas", len(self.cache))
            except Exception as exc:
                logger.warning("Error leyendo caché de embeddings, iniciando vacía: %s", exc)
                self.cache = {}
        else:
            self.cache = {}

    def _save_cache(self):
        """Guarda la caché de embeddings en el disco."""
        try:
            self.sb_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.cache, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("Caché de embeddings guardada en disco: %d notas indexadas", len(self.cache))
        except Exception as exc:
            logger.error("Error guardando caché de embeddings: %s", exc)

    def _get_gemini_client(self) -> Optional[object]:
        """Inicializa y retorna el cliente de Gemini si la API key está disponible."""
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            logger.warning("No se encontró GEMINI_API_KEY. Búsqueda semántica deshabilitada.")
            return None
        try:
            from google import genai
            return genai.Client(api_key=api_key)
        except Exception as exc:
            logger.error("Error inicializando cliente de Gemini en SemanticSearchEngine: %s", exc)
            return None

    def _generate_embedding(self, client, text: str) -> Optional[list[float]]:
        """Genera un embedding para un texto usando Gemini."""
        try:
            response = client.models.embed_content(
                model=self.embedding_model,
                contents=text
            )
            
            # Extraer vector de forma segura contemplando variaciones del SDK
            if hasattr(response, 'embedding') and response.embedding:
                return response.embedding.values
            elif hasattr(response, 'embeddings') and response.embeddings and len(response.embeddings) > 0:
                return response.embeddings[0].values
            else:
                return response.embedding.values
        except Exception as exc:
            logger.error("Error generando embedding con Gemini: %s", exc)
            return None

    def build_index(self) -> dict:
        """
        Escanea las notas de second_brain/, detecta cambios y regenera embeddings ausentes.
        """
        client = self._get_gemini_client()
        if not client:
            return {"status": "error", "reason": "No GEMINI_API_KEY", "indexed": 0}

        if not self.sb_dir.exists():
            return {"status": "error", "reason": "Bóveda no encontrada", "indexed": 0}

        # Escanear todos los archivos md del second brain
        md_files = list(self.sb_dir.rglob("*.md"))
        indexed_count = 0
        skipped_count = 0
        failed_count = 0

        # Para depuración y limpieza: mantener un registro de rutas válidas de esta ejecución
        active_rel_paths = set()

        for md_path in md_files:
            try:
                rel_path = str(md_path.relative_to(self.sb_dir))
            except ValueError:
                rel_path = str(md_path)
            
            # Omitir archivos del sistema o índices demasiado globales si es necesario (mantener todos los MDs)
            active_rel_paths.add(rel_path)
            
            try:
                content = md_path.read_text(encoding="utf-8", errors="ignore").strip()
                if not content:
                    continue

                sha256 = _compute_sha256(content)
                
                # Si ya existe en la caché con el mismo SHA256, omitir
                if rel_path in self.cache and self.cache[rel_path].get("sha256") == sha256:
                    skipped_count += 1
                    continue

                # Generar nuevo embedding
                # Extraemos el contenido útil (podemos omitir metadatos de frontmatter para búsquedas más limpias)
                # pero para conservar contexto, enviamos las primeras partes significativas.
                embedding = self._generate_embedding(client, content)
                if embedding:
                    self.cache[rel_path] = {
                        "name": md_path.name,
                        "title": md_path.stem,
                        "category": md_path.parent.name if md_path.parent != self.sb_dir else "root",
                        "sha256": sha256,
                        "embedding": embedding
                    }
                    indexed_count += 1
                else:
                    failed_count += 1
            except Exception as exc:
                logger.error("Error procesando nota '%s' en el indexador semántico: %s", md_path.name, exc)
                failed_count += 1

        # Limpiar notas eliminadas de la caché
        keys_to_delete = [k for k in self.cache if k not in active_rel_paths]
        for k in keys_to_delete:
            del self.cache[k]

        # Guardar en disco si hubo cambios o limpiezas
        if indexed_count > 0 or len(keys_to_delete) > 0:
            self._save_cache()

        return {
            "status": "success",
            "indexed": indexed_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "deleted": len(keys_to_delete),
            "total_cached": len(self.cache)
        }

    def search(self, query: str, limit: int = 15) -> list[dict]:
        """
        Busca notas semánticamente similares a la consulta del usuario.
        """
        client = self._get_gemini_client()
        if not client:
            return []

        query = query.strip()
        if not query:
            return []

        # Generar embedding de la consulta
        query_emb = self._generate_embedding(client, query)
        if not query_emb:
            return []

        results = []
        for rel_path, info in self.cache.items():
            emb = info.get("embedding")
            if not emb:
                continue

            similarity = _cosine_similarity(query_emb, emb)
            
            # Guardamos el resultado si tiene cierta similitud
            # En text-embedding-004, las similitudes suelen ser > 0.3 para relacionarse
            results.append({
                "name": info["name"],
                "title": info["title"],
                "category": info["category"],
                "path": rel_path,
                "score": round(similarity, 4),
                "pct": round(similarity * 100, 1)
            })

        # Ordenar por similitud de forma descendente
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
