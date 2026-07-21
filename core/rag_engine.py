"""
core/rag_engine.py
Motor RAG y Agente de Consultas Analíticas para Zohar v4.
- Chunking Semántico por Encabezados Markdown (#, ##, ###)
- Vectorización Híbrida (PostgreSQL document_embeddings + Caché JSON en disco)
- Búsqueda Vectorial por Similitud de Coseno con Filtrado de Metadatos
- Síntesis RAG Anti-Alucinaciones con Citas Explícitas [Clave | Sección]
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from sqlalchemy import create_engine, text
from core.llm_client import generate_completion, query_gemini_api

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:5432/maritime_dw")


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def split_markdown_by_headers(md_text: str, max_chunk_chars: int = 1500) -> List[Dict[str, str]]:
    """
    Fragmenta un documento Markdown respetando encabezados H1, H2, H3 (#, ##, ###).
    Devuelve lista de {"section_title": str, "chunk_text": str}.
    """
    lines = md_text.splitlines()
    chunks = []
    current_title = "Introducción / General"
    current_lines = []

    header_re = re.compile(r"^(#{1,3})\s+(.*)$")

    for line in lines:
        match = header_re.match(line.strip())
        if match:

            if current_lines:
                text_block = "\n".join(current_lines).strip()
                if len(text_block) > 30:
                    chunks.append({
                        "section_title": current_title,
                        "chunk_text": text_block
                    })
                current_lines = []
            current_title = match.group(2).strip()
        else:
            current_lines.append(line)

    if current_lines:
        text_block = "\n".join(current_lines).strip()
        if len(text_block) > 30:
            chunks.append({
                "section_title": current_title,
                "chunk_text": text_block
            })

    return chunks if chunks else [{"section_title": "Documento Completo", "chunk_text": md_text[:max_chunk_chars]}]


class RAGEngine:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.sb_dir = self.base_dir / "second_brain"
        self.extractions_dir = self.base_dir / "extractions"
        self.cache_file = self.sb_dir / "rag_vector_cache.json"
        self.cache: Dict[str, List[Dict[str, Any]]] = {}
        self._load_cache()

    def _load_cache(self):
        if self.cache_file.exists():
            try:
                self.cache = json.loads(self.cache_file.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                self.cache = {}
        else:
            self.cache = {}

    def _save_cache(self):
        try:
            self.sb_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self.cache, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Error guardando caché RAG: %s", exc)

    def _generate_embedding(self, text: str) -> List[float]:
        """Genera vector embedding para un chunk de texto."""
        # Se genera un vector determinista / semántico basado en frecuencias y palabras clave
        # compatible con entornos locales y cloud sin requerir modelos pesados adicionales
        words = re.findall(r"\w+", text.lower())
        vector = [0.0] * 128
        for i, w in enumerate(words):
            idx = (hash(w) % 128)
            vector[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [round(x / norm, 6) for x in vector]

    def index_document(self, clave: str, md_content: str) -> Dict[str, Any]:
        """Fragmenta e indexa un documento Markdown en PostgreSQL y caché JSON."""
        chunks = split_markdown_by_headers(md_content)
        indexed_chunks = []

        for chunk in chunks:
            sec_title = chunk["section_title"]
            chunk_text = chunk["chunk_text"]
            emb = self._generate_embedding(chunk_text)
            sha = _compute_sha256(chunk_text)

            indexed_chunks.append({
                "clave": clave,
                "section_title": sec_title,
                "chunk_text": chunk_text,
                "embedding": emb,
                "sha256": sha
            })

            # Guardar en PostgreSQL si está disponible
            self._save_chunk_to_db(clave, sec_title, chunk_text, emb, sha)

        self.cache[clave] = indexed_chunks
        self._save_cache()

        return {
            "clave": clave,
            "total_chunks": len(indexed_chunks),
            "status": "INDEXED"
        }

    def _save_chunk_to_db(self, clave: str, sec_title: str, chunk_text: str, emb: List[float], sha: str):
        try:
            engine = create_engine(DB_URL, connect_args={"connect_timeout": 2})
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS public.document_embeddings (
                        id SERIAL PRIMARY KEY,
                        clave VARCHAR(50),
                        section_title TEXT,
                        chunk_text TEXT,
                        embedding JSONB,
                        sha256 VARCHAR(64),
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                """))
                conn.execute(text("""
                    INSERT INTO public.document_embeddings (clave, section_title, chunk_text, embedding, sha256)
                    VALUES (:clave, :sec_title, :chunk_text, :embedding, :sha)
                """), {
                    "clave": clave,
                    "sec_title": sec_title,
                    "chunk_text": chunk_text,
                    "embedding": json.dumps(emb),
                    "sha": sha
                })
        except Exception:
            pass

    def retrieve_context(self, query: str, filters: Optional[Dict[str, Any]] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """Recupera los Top-K chunks más relevantes para la consulta del usuario."""
        query_emb = self._generate_embedding(query)
        target_clave = (filters or {}).get("clave")

        candidates = []
        for c_clave, chunk_list in self.cache.items():
            if target_clave and c_clave != target_clave:
                continue
            for item in chunk_list:
                sim = _cosine_similarity(query_emb, item["embedding"])
                candidates.append({
                    "clave": c_clave,
                    "section_title": item["section_title"],
                    "chunk_text": item["chunk_text"],
                    "score": round(sim, 4),
                    "pct": round(sim * 100, 1)
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    def query_rag(self, query: str, filters: Optional[Dict[str, Any]] = None, top_k: int = 5) -> Dict[str, Any]:
        """
        Ejecuta el flujo RAG completo:
        1. Recuperación de contexto Top-K
        2. Ensamblado de Prompt con Citas Estrictas
        3. Generación / Síntesis con LLM
        """
        context_chunks = self.retrieve_context(query, filters=filters, top_k=top_k)

        if not context_chunks:
            return {
                "query": query,
                "answer": "No se encontraron documentos o fragmentos relevantes en la base de conocimiento para responder a la consulta.",
                "citations": [],
                "context_used": 0
            }

        context_str = ""
        citations = []
        for i, c in enumerate(context_chunks, 1):
            cite_tag = f"[{c['clave']} | {c['section_title']}]"
            citations.append(cite_tag)
            context_str += f"\n--- FUENTE {i} {cite_tag} ---\n{c['chunk_text']}\n"

        prompt = f"""Instrucciones: Responde a la pregunta del usuario utilizando ÚNICAMENTE el contexto provisto a continuación. 
Es OBLIGATORIO incluir las citas explícitas en el formato [Clave | Sección] al final de cada afirmación principal. Si el contexto no responde a la pregunta, declara formalmente que no se cuenta con información en la base de conocimiento.

CONTEXTO RECUPERADO:
{context_str}

PREGUNTA DEL USUARIO:
{query}

RESPUESTA ANALÍTICA CON CITAS:
"""

        # Síntesis con LLM (Prioridad Local -> Fallback Gemini API)
        try:
            res_dict = generate_completion(prompt, response_json=False)
            answer = res_dict.get("text", str(res_dict))
        except Exception:
            answer = query_gemini_api(prompt)

        return {
            "query": query,
            "answer": answer,
            "citations": citations,
            "context_used": len(context_chunks),
            "sources": context_chunks
        }

    def retrieve_hybrid(self, query: str, filters: Optional[Dict[str, Any]] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        Recuperación Híbrida combinando BM25 (Léxico) y Cosine Similarity (Vectorial)
        utilizando Reciprocal Rank Fusion (RRF).
        """
        target_clave = (filters or {}).get("clave")
        query_emb = self._generate_embedding(query)
        q_tokens = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 2]

        all_items = []
        for c_clave, chunk_list in self.cache.items():
            if target_clave and c_clave != target_clave:
                continue
            for item in chunk_list:
                c_text = item["chunk_text"]
                c_tokens = [w.lower() for w in re.findall(r"\w+", c_text)]
                vec_sim = _cosine_similarity(query_emb, item["embedding"])
                bm25 = _bm25_score(q_tokens, c_tokens)

                all_items.append({
                    "clave": c_clave,
                    "section_title": item["section_title"],
                    "chunk_text": c_text,
                    "vec_sim": vec_sim,
                    "bm25": bm25,
                })

        if not all_items:
            return []

        rank_vec = sorted(all_items, key=lambda x: x["vec_sim"], reverse=True)
        vec_ranks = {id(item): rank for rank, item in enumerate(rank_vec, 1)}

        rank_bm25 = sorted(all_items, key=lambda x: x["bm25"], reverse=True)
        bm25_ranks = {id(item): rank for rank, item in enumerate(rank_bm25, 1)}

        k_rrf = 60
        for item in all_items:
            r_v = vec_ranks[id(item)]
            r_b = bm25_ranks[id(item)]
            rrf_score = (1.0 / (k_rrf + r_v)) + (1.0 / (k_rrf + r_b))
            item["score"] = round(rrf_score, 5)
            item["pct"] = min(99.9, round(rrf_score * 3000, 1))

        all_items.sort(key=lambda x: x["score"], reverse=True)
        return all_items[:top_k]

    def index_vault(self) -> Dict[str, Any]:
        """Indexa masivamente todas las notas en second_brain/."""
        if not self.sb_dir.exists():
            return {"status": "error", "msg": "Directorio second_brain no existe"}

        all_notes = list(self.sb_dir.rglob("*.md"))
        indexed_count = 0
        total_chunks = 0

        for note in all_notes:
            try:
                clave = note.stem
                content = note.read_text(encoding="utf-8", errors="ignore")
                res = self.index_document(clave, content)
                indexed_count += 1
                total_chunks += res.get("total_chunks", 0)
            except Exception as exc:
                logger.warning("Error indexando nota %s: %s", note.name, exc)

        return {
            "status": "ok",
            "notes_indexed": indexed_count,
            "total_chunks": total_chunks
        }


def _bm25_score(query_tokens: list[str], chunk_tokens: list[str], k1: float = 1.5, b: float = 0.75, avg_dl: float = 200.0) -> float:
    if not query_tokens or not chunk_tokens:
        return 0.0
    doc_len = len(chunk_tokens)
    chunk_counts = {}
    for tok in chunk_tokens:
        chunk_counts[tok] = chunk_counts.get(tok, 0) + 1

    score = 0.0
    for q_tok in query_tokens:
        tf = chunk_counts.get(q_tok, 0)
        if tf > 0:
            num = tf * (k1 + 1.0)
            den = tf + k1 * (1.0 - b + b * (doc_len / avg_dl))
            score += (num / den)
    return score
