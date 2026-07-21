"""
core/inference_engine.py
Motor de inferencia "Por Qué Sí / Por Qué No" usando Gemini.
Analiza estudios de impacto ambiental y emite veredicto FAVORABLE/DESFAVORABLE/CONDICIONADO.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Knockouts — Rechazo automático sin análisis Gemini
# ---------------------------------------------------------------------------

KNOCKOUT_PATTERNS = [
    {
        "id":      "anp_categoria_i_iii",
        "label":   "Traslape con ANP categoría I-III",
        "pattern": r"(?i)(zona\s+núcleo|categoría\s+i{1,3}|reserva\s+de\s+biosfera)",
    },
    {
        "id":      "especie_peligro_sin_plan",
        "label":   "Especie en peligro (P) NOM-059 sin plan de manejo",
        "pattern": r"(?i)nom-059.*\bP\b(?!.*plan\s+de\s+manejo)",
    },
]

SYSTEM_PROMPT_GEMINI = """
Eres un evaluador experto de Estudios de Impacto Ambiental (EIA) para proyectos en México,
bajo el marco de la LGEEPA y normas SEMARNAT. Tu tarea es analizar el texto de un estudio
y emitir un veredicto estructurado.

Reglas:
- Emite DESFAVORABLE si hay impactos irreversibles no mitigables o knockouts detectados.
- Emite CONDICIONADO si hay impactos mitigables con medidas claras.
- Emite FAVORABLE si los impactos son mínimos y mitigables fácilmente.
- Lista señales específicas citando fragmentos del texto.
- Sé conciso y técnico. No inventes información.

Responde SIEMPRE en JSON con esta estructura exacta:
{
  "veredicto": "FAVORABLE|DESFAVORABLE|CONDICIONADO",
  "score": 0.0,
  "yes_signals": ["..."],
  "no_signals": ["..."],
  "knockouts": ["..."],
  "condicionantes": ["..."],
  "confianza_pct": 85,
  "meta": {"modelo": "...", "tokens_entrada": 0}
}
"""

SYSTEM_PROMPT_LOCAL = """
Eres un evaluador experto de Estudios de Impacto Ambiental (EIA) en México.
Analiza el texto de un estudio y emite un veredicto estructurado (FAVORABLE, DESFAVORABLE o CONDICIONADO).

Reglas:
- Emite DESFAVORABLE si hay impactos graves irreversibles o knockouts.
- Emite CONDICIONADO si hay impactos mitigables con medidas.
- Emite FAVORABLE si los impactos son mínimos.
- Sé conciso y técnico.

Responde ÚNICAMENTE en JSON con esta estructura exacta:
{
  "veredicto": "FAVORABLE|DESFAVORABLE|CONDICIONADO",
  "score": 0.0,
  "yes_signals": ["señal positiva 1", "señal positiva 2"],
  "no_signals": ["señal negativa 1"],
  "knockouts": [],
  "condicionantes": [],
  "confianza_pct": 80
}
"""


import math
import re

def _tokenize(text: str) -> list[str]:
    """Tokeniza y normaliza un texto a minúsculas, quitando caracteres no alfanuméricos."""
    return re.findall(r'\b\w+\b', text.lower())


class SimpleBM25:
    """Implementación local ligera de BM25 para RAG sin dependencias externas."""
    def __init__(self, corpus: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus)
        self.doc_lengths = [len(_tokenize(doc)) for doc in corpus]
        self.avg_doc_len = sum(self.doc_lengths) / max(self.corpus_size, 1)
        self.docs_tokenized = [_tokenize(doc) for doc in corpus]
        
        # Calcular Document Frequency (DF)
        self.doc_freqs = {}
        for doc_tokens in self.docs_tokenized:
            unique_tokens = set(doc_tokens)
            for token in unique_tokens:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1
                
        # Calcular Inverse Document Frequency (IDF)
        self.idfs = {}
        for token, df in self.doc_freqs.items():
            self.idfs[token] = math.log((self.corpus_size - df + 0.5) / (df + 0.5) + 1.0)

    def score(self, query: str, doc_idx: int) -> float:
        query_tokens = _tokenize(query)
        doc_tokens = self.docs_tokenized[doc_idx]
        doc_len = self.doc_lengths[doc_idx]
        
        tf = {}
        for token in doc_tokens:
            tf[token] = tf.get(token, 0) + 1
            
        score = 0.0
        for token in query_tokens:
            if token not in self.idfs:
                continue
            token_tf = tf.get(token, 0)
            idf = self.idfs[token]
            num = token_tf * (self.k1 + 1)
            den = token_tf + self.k1 * (1.0 - self.b + self.b * (doc_len / max(self.avg_doc_len, 1)))
            score += idf * (num / den)
            
        return score


def _chunk_text(text: str, chunk_size: int = 2000, overlap: int = 300) -> list[str]:
    """Divide el texto en fragmentos superpuestos."""
    chunks = []
    if len(text) <= chunk_size:
        return [text]
    
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def retrieve_relevant_context(text: str) -> str:
    """Realiza búsquedas dirigidas (RAG local) para compilar el contexto más relevante."""
    chunks = _chunk_text(text, chunk_size=2000, overlap=300)
    if len(chunks) <= 8:
        return text
        
    bm25 = SimpleBM25(chunks)
    
    # Consultas temáticas para peinar el estudio
    queries = [
        "área natural protegida reserva de la biosfera parque nacional traslape coordenadas zona núcleo",
        "NOM-059-SEMARNAT especie en peligro amenazada sujeta a protección especial fauna flora endémica",
        "medidas de mitigación riesgos impactos ambientales plan de manejo restauración PTAR suelo agua aire"
    ]
    
    selected_indices = set()
    for q in queries:
        scores = []
        for i in range(len(chunks)):
            scores.append((bm25.score(q, i), i))
        scores.sort(key=lambda x: x[0], reverse=True)
        for _, idx in scores[:2]:
            selected_indices.add(idx)
            
    ordered_indices = sorted(list(selected_indices))
    retrieved_chunks = [chunks[i] for i in ordered_indices]
    
    logger.info("RAG Local: Seleccionados %d de %d fragmentos para análisis.", len(retrieved_chunks), len(chunks))
    return "\n\n=== FRAGMENTO DE ESTUDIO ===\n\n".join(retrieved_chunks)


def _check_knockouts(text: str) -> list[str]:
    """Detecta knockouts automáticos en el texto."""
    triggered = []
    for ko in KNOCKOUT_PATTERNS:
        if re.search(ko["pattern"], text):
            triggered.append(ko["label"])
    return triggered


def _truncate_text(text: str, max_chars: int = 120_000) -> str:
    """Trunca el texto respetando límites de contexto."""
    if len(text) <= max_chars:
        return text
    mid = max_chars // 2
    return text[:mid] + "\n\n[...TEXTO TRUNCADO...]\n\n" + text[-mid:]


def generate_report(md_path: Path) -> dict:
    """
    Genera reporte de inferencia para un estudio de impacto ambiental.

    Returns:
    {
        "veredicto":      "FAVORABLE" | "DESFAVORABLE" | "CONDICIONADO",
        "score":          float,          # 0.0 – 1.0
        "yes_signals":    list[str],
        "no_signals":     list[str],
        "knockouts":      list[str],
        "condicionantes": list[str],
        "confianza_pct":  int,
        "meta":           dict
    }
    """
    md_path = Path(md_path)

    if not md_path.exists():
        return {
            "veredicto": "DESFAVORABLE",
            "score": 0.0,
            "yes_signals": [],
            "no_signals": ["Archivo no encontrado"],
            "knockouts": [],
            "condicionantes": [],
            "confianza_pct": 0,
            "meta": {"error": f"Archivo no encontrado: {md_path}"},
        }

    text = md_path.read_text(encoding="utf-8", errors="replace")

    # Knockout check primero (sin llamada a Gemini/local)
    knockouts = _check_knockouts(text)
    if knockouts:
        return {
            "veredicto": "DESFAVORABLE",
            "score": 0.0,
            "yes_signals": [],
            "no_signals": ["Knockout automático detectado"],
            "knockouts": knockouts,
            "condicionantes": [],
            "confianza_pct": 100,
            "meta": {"source": "knockout_rule", "file": str(md_path)},
        }

    # Llamar al cliente de abstracción de modelos
    try:
        from core.llm_client import detect_active_backend, generate_completion
        provider, model_name = detect_active_backend()
        
        if provider in ("heuristic", "fallback_heuristic"):
            return _fallback_report(text, md_path)
            
        # Prompts y RAG diferenciados
        if provider in ("llama-server", "ollama"):
            sys_prompt = SYSTEM_PROMPT_LOCAL
            context = retrieve_relevant_context(text)
            context = _truncate_text(context, max_chars=15_000)
        else:
            sys_prompt = SYSTEM_PROMPT_GEMINI
            context = _truncate_text(text, max_chars=120_000)
            
        result = generate_completion(
            prompt=context,
            system_prompt=sys_prompt,
            response_json=True
        )
        
        if not result or not isinstance(result, dict) or result.get("is_fallback"):
            return _fallback_report(text, md_path)
            
        result.setdefault("meta", {})
        result["meta"]["file"] = str(md_path)
        return result
    except Exception as exc:
        logger.error("Error en generate_report con llm_client: %s", exc)
        return _fallback_report(text, md_path, error=str(exc))


def _fallback_report(text: str, md_path: Path, error: Optional[str] = None) -> dict:
    """
    Reporte de fallback cuando Gemini no está disponible.
    Usa heurísticas simples basadas en patrones.
    """
    import re

    yes_patterns = [
        r"medidas?\s+de\s+mitigación",
        r"impacto\s+(bajo|mínimo|menor)",
        r"plan\s+de\s+manejo",
        r"restauración\s+ecológica",
    ]
    no_patterns = [
        r"impacto\s+(alto|significativo|grave|irreversible)",
        r"sin\s+medidas?\s+de\s+mitigación",
        r"área\s+natural\s+protegida",
        r"especie\s+en\s+peligro",
    ]

    yes_signals = []
    no_signals = []

    for p in yes_patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            ctx = text[max(0, m.start()-50):m.end()+50].strip()
            yes_signals.append(ctx)
            if len(yes_signals) >= 5:
                break

    for p in no_patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            ctx = text[max(0, m.start()-50):m.end()+50].strip()
            no_signals.append(ctx)
            if len(no_signals) >= 5:
                break

    score = len(yes_signals) / max(len(yes_signals) + len(no_signals), 1)

    if score >= 0.6:
        veredicto = "FAVORABLE"
    elif score >= 0.3:
        veredicto = "CONDICIONADO"
    else:
        veredicto = "DESFAVORABLE"

    return {
        "veredicto": veredicto,
        "score": round(score, 2),
        "yes_signals": yes_signals[:5],
        "no_signals": no_signals[:5],
        "knockouts": [],
        "condicionantes": [],
        "confianza_pct": 40,
        "meta": {
            "source": "fallback_heuristic",
            "file": str(md_path),
            "error": error,
        },
    }
