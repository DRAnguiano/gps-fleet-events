import os
from dotenv import load_dotenv

load_dotenv(override=False)

# =========================
# MODELOS
# =========================

MODEL_NAME = os.getenv("MODEL_NAME", "phi3:mini")

# Mejor retrieval multilenguaje
EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "BAAI/bge-m3"
)

# =========================
# CHUNKING
# =========================

CHUNK_SIZE = os.getenv("CHUNK_SIZE", "700")
CHUNK_OVERLAP = os.getenv("CHUNK_OVERLAP", "120")
TOP_K = os.getenv("TOP_K", "3")

# =========================
# RUTAS
# =========================

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DB_DIR = os.getenv("DB_DIR", "/app/chroma_db")

# =========================
# OLLAMA
# =========================

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://ollama:11434"
)

# =========================
# GENERACIÓN
# =========================

NUM_CTX = int(os.getenv("NUM_CTX", "4096"))

# Menos texto innecesario
NUM_PREDICT = int(os.getenv("NUM_PREDICT", "512"))

# MUCHÍSIMO más estable
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.1"))

REPEAT_PENALTY = float(
    os.getenv("REPEAT_PENALTY", "1.1")
)

# =========================
# UMBRAL DEL RAG
# =========================

# Similitud mínima para que /ask conteste con los documentos en vez de decir
# que no tiene el dato.
#
# El valor depende del modelo de embeddings y hay que recalibrarlo al
# cambiarlo: con all-MiniLM-L6-v2 un acierto claro ronda 0.40-0.50 y el ruido
# se queda en 0.20, así que el 0.60 que había antes descartaba incluso las
# respuestas correctas. Compruébalo con POST /search, que devuelve los scores.
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "0.35"))

# =========================
# BASE OPERATIVA (gps_event)
# =========================

# Si queda vacío, las consultas de flota se desactivan y /ask responde
# únicamente sobre documentos.
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Zona en la que se muestran las horas al usuario. Los eventos se guardan en
# TIMESTAMPTZ, así que esto solo afecta a la presentación.
FLEET_TZ = os.getenv("FLEET_TZ", "America/Monterrey")

# Corta consultas colgadas: el bot responde por Telegram y nadie espera.
FLEET_QUERY_TIMEOUT_S = float(
    os.getenv("FLEET_QUERY_TIMEOUT_S", "5")
)

# A partir de cuántas horas sin lectura se advierte que el dato es viejo.
FLEET_STALE_HOURS = float(
    os.getenv("FLEET_STALE_HOURS", "1")
)

# =========================
# SEGURIDAD
# =========================

REINDEX_API_KEY = os.getenv(
    "REINDEX_API_KEY",
    ""
)

INCLUDE_ERROR_DETAILS = (
    os.getenv(
        "INCLUDE_ERROR_DETAILS",
        "false"
    ).lower() == "true"
)

# =========================
# INDEXACIÓN
# =========================

REINDEX_CLEAN = (
    os.getenv(
        "REINDEX_CLEAN",
        "false"
    ).lower() == "true"
)

INDEX_EXTENSIONS = [
    ext.strip().lower()
    for ext in os.getenv(
        "INDEX_EXTENSIONS",
        ".pdf"
    ).split(",")
    if ext.strip()
]

# =========================
# LLAMAINDEX
# =========================

os.environ["LLAMA_INDEX_DISABLE_EVENT_LOGGING"] = "1"

# =========================
# RAG
# =========================

RAG_HTTP_URL = os.getenv(
    "RAG_HTTP_URL",
    "http://localhost:8000"
)

RAG_SEARCH_ENDPOINT = os.getenv(
    "RAG_SEARCH_ENDPOINT",
    "/search"
)