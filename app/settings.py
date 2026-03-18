import os
from dotenv import load_dotenv

# Carga variables de entorno desde .env solo si no están ya definidas (Docker manda prioridad)
load_dotenv(override=False)

# Modelos
MODEL_NAME = os.getenv("MODEL_NAME", "phi3:mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# Chunking / retrieval (strings; se castean en indexer.py)
CHUNK_SIZE = os.getenv("CHUNK_SIZE", "800")
CHUNK_OVERLAP = os.getenv("CHUNK_OVERLAP", "150")
TOP_K = os.getenv("TOP_K", "4")

# Rutas
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DB_DIR = os.getenv("DB_DIR", "/app/chroma_db")

# Ollama
# ejemplo: http://host.docker.internal:11434  (puede ser None)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")

# ====== NUEVO: Opciones de generación para evitar cortes ======
NUM_CTX = int(os.getenv("NUM_CTX", "4096"))
NUM_PREDICT = int(os.getenv("NUM_PREDICT", "1280"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.30"))
REPEAT_PENALTY = float(os.getenv("REPEAT_PENALTY", "1.05"))

# Desactiva telemetría de LlamaIndex (opcional)
os.environ["LLAMA_INDEX_DISABLE_EVENT_LOGGING"] = "1"
# ==========================
# ENDPOINT PARA VOICE AGENT
# ==========================
RAG_HTTP_URL = os.getenv("RAG_HTTP_URL", "http://localhost:8000")
RAG_SEARCH_ENDPOINT = os.getenv("RAG_SEARCH_ENDPOINT", "/search")

# Seguridad / hardening
REINDEX_API_KEY = os.getenv("REINDEX_API_KEY", "")
INCLUDE_ERROR_DETAILS = os.getenv("INCLUDE_ERROR_DETAILS", "false").lower() == "true"

# Indexación
REINDEX_CLEAN = os.getenv("REINDEX_CLEAN", "false").lower() == "true"
INDEX_EXTENSIONS = [
    ext.strip().lower()
    for ext in os.getenv("INDEX_EXTENSIONS", ".pdf").split(",")
    if ext.strip()
]
