import os
import sys
import json
import argparse
import re
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.orm import declarative_base, sessionmaker

# Agregar el directorio raíz al path para importar correctamente los módulos locales
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.llm_client import generate_completion

# Cargar variables de entorno
load_dotenv()

from core.config import DATABASE_URL, SECOND_BRAIN_DIR
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class SemarnatProject(Base):
    __tablename__ = 'semarnat_projects'
    __table_args__ = {'schema': 'public'}
    
    clave = Column(String, primary_key=True)
    project_name = Column(String)
    status = Column(String)
    sector = Column(String)
    state = Column(String)
    year = Column(Integer)
    promovente = Column(String)

def extract_entities(clave: str) -> dict:
    """
    Lee la nota de la entidad y extrae los campos requeridos en formato JSON.
    """
    note_path = SECOND_BRAIN_DIR / "02_Entities" / f"Proyecto - {clave}.md"
    if not note_path.exists():
        print(f"Error: Nota para la clave {clave} no encontrada en {note_path}", file=sys.stderr)
        return {}

    note_content = note_path.read_text(encoding="utf-8")

    # PROMPT DE EXTRACCIÓN V0 (Este bloque de prompt es lo que optimizará el meta-agente)
    prompt = f"""
Analiza la siguiente nota estructurada de un proyecto ambiental de SEMARNAT y extrae las entidades solicitadas.
Devuelve únicamente un objeto JSON con estas llaves exactas:
- Clave
- Promovente
- Localidad
- Municipio
- Estado
- Tipo_MIA

Si un dato no se menciona en la nota, pon "Desconocido".

Nota a analizar:
\"\"\"
{note_content}
\"\"\"

Respuesta JSON (sin markdown, sin explicaciones):
"""

    try:
        # Usar response_json=False para evitar excepciones de parsing de llm_client al meter tags de Gemma
        result = generate_completion(
            prompt=prompt,
            response_json=False
        )
        
        raw_text = result.get("text", "") if isinstance(result, dict) else ""
        
        cleaned = raw_text.strip()
        # Remover posibles tags de turnos sobrantes de Gemma
        for tag in ["</start_of_turn>", "<end_of_turn>", "<eos>", "<bos>"]:
            cleaned = cleaned.replace(tag, "")
        cleaned = cleaned.strip()

        # Encontrar y extraer el objeto JSON usando regex
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception as e:
                print(f"Error parseando JSON extraído: {e}. Texto limpio: {cleaned}", file=sys.stderr)
        else:
            print(f"No se encontró un bloque JSON en la respuesta: {raw_text}", file=sys.stderr)
            
        return {}
    except Exception as exc:
        print(f"Error llamando a generate_completion: {exc}", file=sys.stderr)
        return {}

def persist_to_db(clave: str, data: dict):
    """
    Guarda los metadatos extraídos en la base de datos PostgreSQL utilizando SQLAlchemy.
    """
    if not data:
        return
    
    db = SessionLocal()
    try:
        project = db.query(SemarnatProject).filter(SemarnatProject.clave == clave).first()
        
        # Mapeo de datos del JSON de extracción a columnas de la BD
        promovente = data.get("Promovente", "Desconocido")
        estado = data.get("Estado", "Desconocido")
        
        if project:
            if promovente != "Desconocido":
                project.promovente = promovente
            if estado != "Desconocido":
                project.state = estado
        else:
            project = SemarnatProject(
                clave=clave,
                project_name=f"Proyecto {clave}",
                status="INGRESADO",
                state=estado,
                promovente=promovente,
                year=2026
            )
            db.add(project)
        db.commit()
    except Exception as e:
        print(f"Warning: No se pudo persistir en la BD: {e}", file=sys.stderr)
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agente Extractor de Entidades Zohar v0")
    parser.add_argument("--clave", type=str, required=True, help="Clave del proyecto a procesar")
    args = parser.parse_args()

    extracted = extract_entities(args.clave)
    
    if extracted:
        # Persistir de forma segura en Postgres
        persist_to_db(args.clave, extracted)
        # Imprimir resultado en stdout para eval_zohar.py
        print(json.dumps(extracted, ensure_ascii=False))
    else:
        print(json.dumps({}))
