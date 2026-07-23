import pytest
from core.rag_engine import split_markdown_by_headers, RAGEngine

def test_hierarchical_chunking_small_block():
    """Si el texto de la sección es menor que max_chunk_chars, no debe dividirse."""
    md = """# Proyecto Turístico
Esta es una descripción del proyecto turístico largo para pasar el filtro de treinta caracteres."""
    chunks = split_markdown_by_headers(md, max_chunk_chars=150)
    assert len(chunks) == 1
    assert chunks[0]["section_title"] == "Proyecto Turístico"
    assert "Esta es una descripción del proyecto turístico largo para pasar el filtro de treinta caracteres." in chunks[0]["chunk_text"]

def test_hierarchical_chunking_large_block_splits_with_overlap():
    """Un texto largo que excede max_chunk_chars debe dividirse en sub-chunks con overlap e inyectar el título."""
    # max_chunk_chars=50, overlap_chars=20
    # Texto de 100 caracteres
    long_text = "ABCDEFGHIJ" * 10 # 100 caracteres
    md = f"""# Gran Sección
{long_text}"""
    chunks = split_markdown_by_headers(md, max_chunk_chars=50, overlap_chars=20)
    
    # Debe dividirse en al menos 2 partes
    assert len(chunks) >= 2
    
    # Cada parte debe iniciar con el prefijo de la sección inyectada
    assert chunks[0]["section_title"] == "Gran Sección (Parte 1)"
    assert chunks[0]["chunk_text"].startswith("[Sección: Gran Sección]")
    
    assert chunks[1]["section_title"] == "Gran Sección (Parte 2)"
    assert chunks[1]["chunk_text"].startswith("[Sección: Gran Sección]")
    
    # Comprobar que hay solapamiento (overlap) en el contenido
    # Parte 1 contendrá caracteres del inicio. Parte 2 del medio.
    # Con max_chunk_chars=50, el slice es de 50 chars.
    # El slice de la Parte 2 empieza en start = (50 - 20) = 30.
    # Así que los caracteres de la posición 30 a 50 deben estar en ambos.
    chunk1_body = chunks[0]["chunk_text"].replace("[Sección: Gran Sección] ", "")
    chunk2_body = chunks[1]["chunk_text"].replace("[Sección: Gran Sección] ", "")
    
    overlap_part = chunk1_body[30:50]
    assert overlap_part in chunk2_body

def test_rag_query_with_hierarchical_citations(tmp_path):
    """Verifica que la consulta RAG utilice e indique los sub-chunks jerárquicos correctos."""
    engine = RAGEngine(base_dir=tmp_path)
    
    # Documento con sección muy larga
    long_text = "Este es un estudio de impacto ambiental para proteger el manglar. " * 30 # ~1900 caracteres
    md = f"""# Proyecto Manglar
## Conservación de Flora
{long_text}"""
    
    # Indexar con un max_chunk_chars pequeño en el chunker para forzar la división jerárquica
    # Nota: index_document usa los defaults (1500), pero podemos inyectar un documento ya pre-calculado
    # o probar que split_markdown_by_headers con valores por defecto divide textos que excedan 1500.
    chunks = split_markdown_by_headers(md, max_chunk_chars=1000, overlap_chars=200)
    assert len(chunks) > 1
    
    # Indexar normalmente y ver si genera partes
    engine.index_document("09PU2026I0001", md)
    
    # Recuperar
    results = engine.retrieve_context("proteger el manglar", top_k=5)
    assert len(results) >= 2
    # El título de la sección de los resultados debe contener "(Parte"
    titles = [r["section_title"] for r in results]
    assert any("(Parte" in t for t in titles)
