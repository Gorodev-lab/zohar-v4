"""
core/semantic_search.py
Motor de búsqueda semántica híbrido (BM25 + Vectorial) y Re-ranking para notas del Second Brain de Zohar v4.
"""

from __future__ import annotations

import json
import logging
import math
import os
import hashlib
from pathlib import Path
from typing import Optional

from core.rag_engine import split_markdown_by_headers, _bm25_score

logger = logging.getLogger(__name__)

_LLM_EMBEDDING_SUPPORTED = None

def _is_llm_embedding_supported() -> bool:
    global _LLM_EMBEDDING_SUPPORTED
    if _LLM_EMBEDDING_SUPPORTED is not None:
        return _LLM_EMBEDDING_SUPPORTED
    import sys
    if "pytest" in sys.modules:
        _LLM_EMBEDDING_SUPPORTED = False
        return False
    import requests
    local_url = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
    try:
        r = requests.post(f"{local_url}/embedding", json={"content": "test"}, timeout=0.5)
        if r.status_code == 200:
            _LLM_EMBEDDING_SUPPORTED = True
            return True
    except Exception:
        pass
    _LLM_EMBEDDING_SUPPORTED = False
    return False

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
            logger.error("Error guardando caché de embeddings: %s", exc)

    def _generate_embedding(self, text: str) -> Optional[list[float]]:
        """Genera un embedding para un texto usando el llama-server local o fallback."""
        truncated_text = text[:1000].strip()
        if _is_llm_embedding_supported():
            import requests
            local_url = os.environ.get("LOCAL_LLM_URL", "http://127.0.0.1:8083")
            try:
                r = requests.post(
                    f"{local_url}/embedding",
                    json={"content": truncated_text},
                    timeout=5.0
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
            except Exception:
                pass
        return self._generate_fallback_embedding(truncated_text)

    def _generate_fallback_embedding(self, text: str) -> list[float]:
        """Generador determinista alternativo si el llama-server no soporta embeddings."""
        import re
        words = re.findall(r"\w+", text.lower())
        vector = [0.0] * 128
        for w in words:
            idx = (hash(w) % 128)
            vector[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [round(x / norm, 6) for x in vector]

    def build_index(self) -> dict:
        """
        Escanea las notas de second_brain/, las divide en chunks y regenera embeddings.
        """
        if not self.sb_dir.exists():
            return {"status": "error", "reason": "Bóveda no encontrada", "indexed": 0}

        # Generar un embedding de prueba para determinar la dimensión esperada
        test_emb = self._generate_embedding("prueba")
        if not test_emb:
            return {"status": "error", "reason": "No se pudo generar embedding de prueba", "indexed": 0}
        
        expected_dim = len(test_emb)
        
        # Limpiar la caché si tiene dimensiones incorrectas o formato desactualizado
        invalidated_count = 0
        keys_to_clean = []
        for k, v in list(self.cache.items()):
            chunks = v.get("chunks", [])
            if not chunks:
                keys_to_clean.append(k)
                continue
            
            invalid = False
            for ch in chunks:
                emb = ch.get("embedding")
                if not emb or len(emb) != expected_dim:
                    invalid = True
                    break
            if invalid:
                keys_to_clean.append(k)
        
        for k in keys_to_clean:
            del self.cache[k]
            invalidated_count += 1
            
        if invalidated_count > 0:
            logger.info("Se invalidaron %d embeddings por cambio de dimensión / formato", invalidated_count)

        # Escanear archivos md del second brain
        md_files = list(self.sb_dir.rglob("*.md"))
        indexed_count = 0
        skipped_count = 0
        failed_count = 0
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
                
                # Si ya existe con el mismo SHA256, omitir
                if rel_path in self.cache and self.cache[rel_path].get("sha256") == sha256:
                    skipped_count += 1
                    continue

                # Dividir el documento en chunks semánticos
                doc_chunks = split_markdown_by_headers(content)
                processed_chunks = []
                failed_chunk = False
                
                for chunk in doc_chunks:
                    chunk_text = chunk["chunk_text"]
                    chunk_sha = _compute_sha256(chunk_text)
                    
                    embedding = self._generate_embedding(chunk_text)
                    if embedding:
                        processed_chunks.append({
                            "section_title": chunk["section_title"],
                            "chunk_text": chunk_text,
                            "sha256": chunk_sha,
                            "embedding": embedding
                        })
                    else:
                        failed_chunk = True
                        break

                if processed_chunks and not failed_chunk:
                    self.cache[rel_path] = {
                        "name": md_path.name,
                        "title": md_path.stem,
                        "category": md_path.parent.name if md_path.parent != self.sb_dir else "root",
                        "sha256": sha256,
                        "chunks": processed_chunks
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
        Busca notas semánticamente similares usando búsqueda híbrida (BM25 + Cosine) y re-ranking con LLM.
        """
        query = query.strip()
        if not query:
            return []

        # 1. Generar embedding de la consulta
        query_emb = self._generate_embedding(query)
        if not query_emb:
            return []

        # 2. Tokenizar la consulta para BM25
        import re
        q_tokens = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]

        candidates = []
        for rel_path, info in self.cache.items():
            chunks = info.get("chunks", [])
            for chunk in chunks:
                emb = chunk.get("embedding")
                if not emb or len(emb) != len(query_emb):
                    continue

                # Similitud vectorial
                vec_sim = _cosine_similarity(query_emb, emb)

                # Similitud léxica BM25
                c_text = chunk["chunk_text"]
                c_tokens = [w.lower() for w in re.findall(r"\w+", c_text)]
                bm25 = _bm25_score(q_tokens, c_tokens)

                candidates.append({
                    "name": info["name"],
                    "title": info["title"],
                    "category": info["category"],
                    "path": rel_path,
                    "section_title": chunk["section_title"],
                    "chunk_text": c_text,
                    "vec_sim": vec_sim,
                    "bm25": bm25
                })

        if not candidates:
            return []

        # 3. Combinación por RRF
        rank_vec = sorted(candidates, key=lambda x: x["vec_sim"], reverse=True)
        vec_ranks = {id(item): rank for rank, item in enumerate(rank_vec, 1)}

        rank_bm25 = sorted(candidates, key=lambda x: x["bm25"], reverse=True)
        bm25_ranks = {id(item): rank for rank, item in enumerate(rank_bm25, 1)}

        k_rrf = 60
        for item in candidates:
            r_v = vec_ranks[id(item)]
            r_b = bm25_ranks[id(item)]
            rrf_score = (1.0 / (k_rrf + r_v)) + (1.0 / (k_rrf + r_b))
            item["score"] = round(rrf_score, 5)
            item["pct"] = min(99.9, round(rrf_score * 3000, 1))

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # 4. Re-ranking con LLM local
        reranked = self.rerank_candidates(query, candidates, top_n=limit)
        return reranked

    def rerank_candidates(self, query: str, candidates: list[dict], top_n: int = 5) -> list[dict]:
        """
        Re-ordena los candidatos utilizando un Cross-Encoder basado en el LLM local (Gemma-4).
        """
        if not candidates:
            return []

        # Seleccionar el top de candidatos para re-ordenar (max 10 para baja latencia)
        candidates_to_rank = candidates[:10]

        from core.llm_client import generate_completion

        # Construir prompt compacto y estructurado
        prompt = f"""Instrucciones: Evalúa críticamente la relevancia de los siguientes fragmentos con respecto a la consulta del usuario.
Asigna una puntuación numérica de relevancia estrictamente de 0.0 a 10.0 para cada fragmento (donde 10.0 es extremadamente relevante y responde directamente a la pregunta, y 0.0 es totalmente irrelevante).

CONSULTA DEL USUARIO:
"{query}"

FRAGMENTOS DE DOCUMENTOS:
"""
        for i, c in enumerate(candidates_to_rank):
            text_snippet = c.get("chunk_text") or c.get("title") or ""
            # Limitar el fragmento a 200 caracteres para controlar el tamaño de ventana
            truncated_snippet = text_snippet[:200].replace("\n", " ")
            prompt += f"ID: {i} | TÍTULO: {c.get('title')} > {c.get('section_title')} | FRAGMENTO: {truncated_snippet}\n"

        prompt += """
Retorna ÚNICAMENTE un objeto JSON en el siguiente formato, sin explicaciones ni markdown fuera del JSON:
{
  "scores": [
    {"id": 0, "score": 8.5},
    {"id": 1, "score": 1.2}
  ]
}
"""
        try:
            res = generate_completion(
                prompt,
                response_json=True,
                system_prompt="Eres un calificador e indexador de relevancia de información preciso y conciso.",
                n_predict=256
            )
            scores_list = res.get("scores", [])
            scores_map = {int(item["id"]): float(item["score"]) for item in scores_list if "id" in item and "score" in item}

            for i, c in enumerate(candidates_to_rank):
                llm_score = scores_map.get(i)
                if llm_score is not None:
                    # Mezclar de forma equilibrada: 70% peso al juicio del LLM, 30% a RRF
                    combined_score = (float(llm_score) / 10.0) * 0.7 + c["score"] * 0.3
                    c["score"] = round(combined_score, 4)
                    c["pct"] = round(combined_score * 100, 1)
                else:
                    # Penalización ligera si el LLM omitió puntuarlo
                    c["score"] = round(c["score"] * 0.5, 4)
                    c["pct"] = round(c["score"] * 100, 1)
        except Exception as exc:
            logger.warning("Fallo en LLM re-ranking, usando scores híbridos originales: %s", exc)
            pass

        # Re-ordenar finales por el score combinado
        candidates_to_rank.sort(key=lambda x: x["score"], reverse=True)
        return candidates_to_rank[:top_n]

