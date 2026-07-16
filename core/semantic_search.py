"""
core/semantic_search.py
Motor de búsqueda semántica para las notas del Second Brain utilizando el llama-server local y caché en disco.
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
        
        # Modelo para embeddings local
        self.embedding_model = "gemma-4-e2b-local"
        
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
            logger.error("Error guando caché de embeddings: %s", exc)

    def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """Genera un embedding para un texto usando el llama-server local."""
        import requests
        local_url = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
        # Truncar a los primeros 1000 caracteres para no exceder los límites de tokens del servidor local y acelerar procesamiento en CPU
        truncated_text = text[:1000].strip()
        try:
            r = requests.post(
                f"{local_url}/embedding",
                json={"content": truncated_text},
                timeout=120.0
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    emb = data[0].get("embedding")
                    if isinstance(emb, list) and len(emb) > 0 and isinstance(emb[0], list):
                        return emb[0]
                    return emb
                elif isinstance(data, dict):
                    return data.get("embedding")
                logger.error("Formato de respuesta de embeddings desconocido: %s", type(data))
            else:
                logger.error("Error del servidor llama-server local (código %d): %s", r.status_code, r.text)
        except Exception as exc:
            logger.error("Error conectando con llama-server para embeddings: %s", exc)
        return None

    def build_index(self) -> dict:
        """
        Escanea las notas de second_brain/, detecta cambios y regenera embeddings ausentes.
        """
        if not self.sb_dir.exists():
            return {"status": "error", "reason": "Bóveda no encontrada", "indexed": 0}

        # Generar un embedding de prueba para determinar la dimensión esperada e invalidar caché vieja si difiere
        test_emb = self._generate_embedding("prueba")
        if not test_emb:
            return {"status": "error", "reason": "No se pudo conectar con el servidor LLM local o no soporta embeddings", "indexed": 0}
        
        expected_dim = len(test_emb)
        
        # Filtrar o limpiar la caché si tiene dimensiones incorrectas (ej: residuos de Gemini text-embedding-004)
        invalidated_count = 0
        keys_to_clean = []
        for k, v in list(self.cache.items()):
            emb = v.get("embedding")
            if not emb or len(emb) != expected_dim:
                keys_to_clean.append(k)
        
        for k in keys_to_clean:
            del self.cache[k]
            invalidated_count += 1
            
        if invalidated_count > 0:
            logger.info("Se invalidaron %d embeddings por cambio de dimensión / modelo de embeddings", invalidated_count)

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

                # Generar nuevo embedding local
                embedding = self._generate_embedding(content)
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

        # Guardar en disco si hubo cambios, invalidaciones o limpiezas
        if indexed_count > 0 or len(keys_to_delete) > 0 or invalidated_count > 0:
            self._save_cache()

        return {
            "status": "success",
            "indexed": indexed_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "deleted": len(keys_to_delete),
            "invalidated": invalidated_count,
            "total_cached": len(self.cache)
        }

    def search(self, query: str, limit: int = 15) -> list[dict]:
        """
        Busca notas semánticamente similares a la consulta del usuario usando embeddings locales.
        """
        query = query.strip()
        if not query:
            return []

        # Generar embedding de la consulta
        query_emb = self._generate_embedding(query)
        if not query_emb:
            return []

        results = []
        for rel_path, info in self.cache.items():
            emb = info.get("embedding")
            if not emb or len(emb) != len(query_emb):
                continue

            similarity = _cosine_similarity(query_emb, emb)
            
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
