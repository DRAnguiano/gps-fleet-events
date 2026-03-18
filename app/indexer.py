from __future__ import annotations
import os
from typing import Optional, List, Dict
import warnings
from .persona_config import SYSTEM_PROMPT

# --- Parche global: clean_up_tokenization_spaces=False en todos los decode() de HF ---
try:
    from transformers.tokenization_utils_base import PreTrainedTokenizerBase
    warnings.filterwarnings(
        "ignore",
        message="`clean_up_tokenization_spaces` was not set",
        category=FutureWarning,
    )
    _ORIG_DECODE = PreTrainedTokenizerBase.decode

    def _patched_decode(self, *args, **kwargs):
        # Fuerza el valor recomendado por el issue de HF: False
        kwargs.setdefault("clean_up_tokenization_spaces", False)
        return _ORIG_DECODE(self, *args, **kwargs)

    PreTrainedTokenizerBase.decode = _patched_decode  # monkeypatch global
except Exception:
    # Si no está instalado transformers todavía, seguimos sin romper.
    pass

# --- Imports LlamaIndex / Chroma / Ollama ---
from llama_index.core import (
    Settings,
    VectorStoreIndex,
    StorageContext,
    SimpleDirectoryReader,
)
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
from chromadb import PersistentClient
from chromadb.config import Settings as ChromaSettings
import ollama  # cliente python

# ✅ NUEVO: detección segura de GPU
import torch


def _detect_device() -> str:
    """Detecta si hay GPU CUDA disponible y devuelve 'cuda' o 'cpu'."""
    try:
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            print(f"[torch] GPU detectada: {name}")
            return "cuda"
        else:
            print("[torch] No se detectó GPU, usando CPU.")
            return "cpu"
    except Exception as e:
        print(f"[torch] Error detectando dispositivo: {type(e).__name__}: {e}")
        return "cpu"


DEVICE = _detect_device()  # usado globalmente


# --- Settings con fallback a ENV ---
try:
    from .settings import (
        MODEL_NAME,
        EMBEDDING_MODEL,
        CHUNK_SIZE,
        CHUNK_OVERLAP,
        TOP_K,
        DATA_DIR,
        DB_DIR,
        OLLAMA_HOST,
        REINDEX_CLEAN,
        INDEX_EXTENSIONS,
        # Opciones de generación (visibles en settings.py también)
        NUM_CTX,
        NUM_PREDICT,
        TEMPERATURE,
        REPEAT_PENALTY,
    )
except Exception:
    MODEL_NAME = os.getenv("MODEL_NAME", "phi3:mini")
    EMBEDDING_MODEL = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    CHUNK_SIZE = os.getenv("CHUNK_SIZE", "800")
    CHUNK_OVERLAP = os.getenv("CHUNK_OVERLAP", "150")
    TOP_K = os.getenv("TOP_K", "4")
    DATA_DIR = os.getenv("DATA_DIR", "/app/data")
    DB_DIR = os.getenv("DB_DIR", "/app/chroma_db")
    # DEFAULT: que apunte al servicio de Docker "ollama"
    OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://ollama:11434")
    REINDEX_CLEAN = os.getenv("REINDEX_CLEAN", "false").lower() == "true"
    INDEX_EXTENSIONS = [
        ext.strip().lower()
        for ext in os.getenv("INDEX_EXTENSIONS", ".pdf").split(",")
        if ext.strip()
    ]

    # ====== NUEVO: opciones generación desde ENV ======
    NUM_CTX = int(os.getenv("NUM_CTX", "4096"))
    NUM_PREDICT = int(os.getenv("NUM_PREDICT", "1280"))
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.30"))
    REPEAT_PENALTY = float(os.getenv("REPEAT_PENALTY", "1.05"))


# -------------------- Caché simple de índice --------------------
_INDEX_CACHE: Optional[VectorStoreIndex] = None
_EMBEDDINGS_READY = False
_LLM_READY = False
_VERIFIED_OLLAMA_MODELS: set[tuple[str, str | None]] = set()

def _get_index_cached(db_dir: Optional[str] = None) -> VectorStoreIndex:
    """
    Devuelve un VectorStoreIndex reutilizando una copia en memoria.
    Así evitamos reconstruir objetos pesados en cada request.
    """
    global _INDEX_CACHE
    db_dir = db_dir or DB_DIR
    if _INDEX_CACHE is None:
        print("[cache] Creando índice en memoria desde Chroma...")
        _INDEX_CACHE = _index_from_existing(db_dir=db_dir)
    else:
        print("[cache] Reutilizando índice en memoria.")
    return _INDEX_CACHE


# -------------------- Utilidades --------------------
def _to_int(val, default: int) -> int:
    """Convierte valor a int de forma segura."""
    try:
        if val is None:
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def _normalize_model_name(name: str) -> str:
    """Acepta alias comunes y devuelve el nombre exacto que entiende Ollama."""
    n = (name or "").strip()
    aliases = {
        "phi3": "phi3:mini",
        "phi3-mini": "phi3:mini",
        "phi3:mini-instruct": "phi3:mini",
    }
    return aliases.get(n, n)


def _ensure_ollama_model(name: str, base_url: str | None):
    """Verifica que el modelo exista en Ollama. Si no existe, intenta `pull`."""
    model_key = (name, base_url)
    if model_key in _VERIFIED_OLLAMA_MODELS:
        return

    client = ollama.Client(host=base_url) if base_url else ollama
    try:
        client.show(name)  # lanza error si no existe
        print(f"[ollama] Modelo disponible: {name}")
    except Exception as e:
        print(
            f"[ollama] '{name}' no encontrado. Haciendo pull... ({type(e).__name__}: {e})"
        )
        client.pull(name)
        print(f"[ollama] Pull completo: {name}")
    _VERIFIED_OLLAMA_MODELS.add(model_key)


# -------------------- Configuración global --------------------
def _configure_embeddings_only() -> None:
    """Configura SOLO embeddings y chunking (sin LLM), con soporte GPU."""
    global _EMBEDDINGS_READY
    if _EMBEDDINGS_READY:
        return

    print(f"[embeddings] Usando modelo: {EMBEDDING_MODEL}")
    print(f"[embeddings] Dispositivo seleccionado: {DEVICE}")

    Settings.embed_model = HuggingFaceEmbedding(
        model_name=EMBEDDING_MODEL,
        device=DEVICE,  # fuerza GPU si está disponible
    )
    Settings.chunk_size = _to_int(CHUNK_SIZE, 800)
    Settings.chunk_overlap = _to_int(CHUNK_OVERLAP, 150)
    _EMBEDDINGS_READY = True


def _configure_llm_only() -> None:
    """Configura el LLM de Ollama con parámetros que evitan cortes."""
    global _LLM_READY
    if _LLM_READY:
        return

    model = _normalize_model_name(MODEL_NAME)
    base_url = OLLAMA_HOST

    _ensure_ollama_model(model, base_url)

    llm_kwargs = {
        "model": model,
        "request_timeout": 600.0,
        "temperature": TEMPERATURE,
        "num_ctx": NUM_CTX,
        "num_predict": NUM_PREDICT,
        "repeat_penalty": REPEAT_PENALTY,
        "keep_alive": "10m",
        "system_prompt": SYSTEM_PROMPT,  # 👈 aquí entra tu persona
    }
    if base_url:
        llm_kwargs["base_url"] = base_url

    print(
        f"[ollama] Configurando modelo {model} "
        f"(num_ctx={NUM_CTX}, num_predict={NUM_PREDICT})..."
    )
    Settings.llm = Ollama(**llm_kwargs)
    _LLM_READY = True



def _chroma_vector_store(db_dir: str, clean: bool = False) -> ChromaVectorStore:
    """Vector store Chroma persistente (sin REST), con opción de limpieza controlada."""
    os.makedirs(db_dir, exist_ok=True)
    client = PersistentClient(
        path=db_dir,
        settings=ChromaSettings(
            anonymized_telemetry=False,
            allow_reset=True,
        ),
    )
    print(f"[chroma] PersistentClient path={db_dir}")

    if clean:
        try:
            client.delete_collection("rag_docs")
            print("[chroma] Colección 'rag_docs' borrada (reindex limpio).")
        except Exception as e:
            print(
                f"[chroma] Aviso al borrar colección: {type(e).__name__}: {e}"
            )

    collection = client.get_or_create_collection("rag_docs")
    return ChromaVectorStore(chroma_collection=collection)


# -------------------- Indexación & Query helpers --------------------
def build_index(
    data_dir: Optional[str] = None,
    db_dir: Optional[str] = None,
) -> VectorStoreIndex:
    """Reconstruye el índice desde archivos en data_dir."""
    global _INDEX_CACHE

    data_dir = data_dir or DATA_DIR
    db_dir = db_dir or DB_DIR

    _configure_embeddings_only()

    reader = SimpleDirectoryReader(
        input_dir=data_dir,
        recursive=True,
        required_exts=INDEX_EXTENSIONS or [".pdf"],
        filename_as_id=True,
        file_metadata=lambda p: {"source": os.path.basename(p)},
    )
    docs = reader.load_data()

    # Limpia SOLO cuando reindexas (controlado vía REINDEX_CLEAN)
    vector_store = _chroma_vector_store(db_dir, clean=REINDEX_CLEAN)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_documents(
        docs,
        storage_context=storage_context,
        show_progress=True,
    )

    # 🔄 actualiza caché en memoria para nuevas queries
    _INDEX_CACHE = index
    print("[cache] Índice en memoria actualizado después de build_index().")

    return index



def _index_from_existing(db_dir: Optional[str] = None) -> VectorStoreIndex:
    """Construye un índice a partir de la base ya persistida (NO reindexa archivos)."""
    db_dir = db_dir or DB_DIR
    vector_store = _chroma_vector_store(db_dir, clean=False)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context,
    )
    return index


def get_query_engine(
    db_dir: Optional[str] = None,
    top_k: Optional[int] = None,
):
    """
    Devuelve un query engine listo para usar (retrieval + LLM).
    Compat con /query legacy; para /ask preferimos call_llm con prompt propio.
    """
    db_dir = db_dir or DB_DIR

    k_env = _to_int(TOP_K, 4)
    k = _to_int(top_k, k_env)
    if k <= 0:
        k = 4

    _configure_embeddings_only()
    _configure_llm_only()

    index = _get_index_cached(db_dir=db_dir)
    return index.as_query_engine(similarity_top_k=_to_int(k, 4))


def get_retriever(
    db_dir: Optional[str] = None,
    top_k: Optional[int] = None,
):
    """Devuelve un retriever que NO depende de Ollama (solo recuperación)."""
    db_dir = db_dir or DB_DIR

    k_env = _to_int(TOP_K, 4)
    k = _to_int(top_k, k_env)
    if k <= 0:
        k = 4

    _configure_embeddings_only()

    index = _get_index_cached(db_dir=db_dir)
    return index.as_retriever(similarity_top_k=_to_int(k, 4))



def retrieve_context_for_guardrail(
    user_query: str,
    db_dir: Optional[str] = None,
    top_k: Optional[int] = None,
) -> List[Dict]:
    """
    Recupera chunks similares con su score para que /ask o /query
    puedan decidir prompts o fallback sin llamar al LLM primero.
    """
    db_dir = db_dir or DB_DIR

    k_env = _to_int(TOP_K, 4)
    k = _to_int(top_k, k_env)
    if k <= 0:
        k = 4

    _configure_embeddings_only()

    index = _get_index_cached(db_dir=db_dir)
    retriever = index.as_retriever(similarity_top_k=_to_int(k, 4))
    nodes = retriever.retrieve(user_query)

    out = []
    for n in nodes:
        meta = getattr(n.node, "metadata", {}) or {}
        out.append(
            {
                "score": getattr(n, "score", None),
                "text": n.node.get_content()[:800],
                "source": meta.get("source"),
                "id": n.node.node_id,
            }
        )
    return out


# -------------------- LLM raw call helper --------------------
def call_llm(prompt: str) -> str:
    """
    Llama directamente al modelo Ollama configurado en Settings.llm
    y devuelve solo el texto (NO streaming). Evita palabras "cortadas".
    """
    _configure_llm_only()  # aseguramos que Settings.llm esté listo
    llm = Settings.llm
    # Usamos .complete() que devuelve .text en versiones estables.
    resp = llm.complete(prompt)
    if hasattr(resp, "text"):
        return resp.text
    return str(resp)
