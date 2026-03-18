from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, ORJSONResponse
from pydantic import BaseModel
from .indexer import build_index, get_query_engine, get_retriever, _to_int, call_llm, retrieve_context_for_guardrail
import os, sys, time, traceback
from fastapi import Header
from .settings import REINDEX_API_KEY, INCLUDE_ERROR_DETAILS

app = FastAPI(default_response_class=ORJSONResponse)

# ---------- Utils ----------
def _sanitize(s: str) -> str:
    if not s:
        return s
    s = s.replace("\uFFFD", "")                  # rombo negro
    return s.encode("utf-8", "ignore").decode("utf-8").strip()

def _split_for_telegram(text: str, limit: int = 3500):
    parts = []
    t = text or ""
    while len(t) > limit:
        cut = t.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(t[:cut])
        t = t[cut:]
    if t:
        parts.append(t)
    return parts

def _public_error(exc: Exception) -> str:
    if INCLUDE_ERROR_DETAILS:
        return f"{type(exc).__name__}: {exc}"
    return "internal_error"

# ---------- Health ----------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------- Reindex ----------
class ReindexBody(BaseModel):
    top_k: int | None = None

@app.post("/reindex")
def reindex(
    body: ReindexBody | None = None,
    k: int | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
):
    if REINDEX_API_KEY and x_api_key != REINDEX_API_KEY:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    print(">>> /reindex llamado", file=sys.stderr)
    t0 = time.time()
    env_top_k = os.getenv("TOP_K")
    env_chunk = os.getenv("CHUNK_SIZE")
    env_overlap = os.getenv("CHUNK_OVERLAP")
    print(f"ENV TOP_K={env_top_k!r} CHUNK_SIZE={env_chunk!r} CHUNK_OVERLAP={env_overlap!r}", file=sys.stderr)

    top_k = _to_int(k, _to_int(getattr(body, "top_k", None), 4))
    print(f"top_k efectivo={top_k}", file=sys.stderr)

    try:
        print(">>> build_index()...", file=sys.stderr)
        build_index()
        print(">>> get_query_engine()...", file=sys.stderr)
        _ = get_query_engine(top_k=top_k)
        print(">>> OK /reindex", file=sys.stderr)
        return {"status": "ok", "top_k": top_k, "elapsed_s": round(time.time() - t0, 2)}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(">>> ERROR /reindex:", err, file=sys.stderr)
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "top_k": top_k,
                "error": _public_error(e),
                "elapsed_s": round(time.time() - t0, 2),
            },
        )

# ---------- Query (RAG con fallback LLM desactivado) ----------
class QueryBody(BaseModel):
    q: str
    top_k: int | None = None

@app.post("/query")
def query(body: QueryBody):
    try:
        engine = get_query_engine(top_k=body.top_k)
        resp = engine.query(body.q)
        text = _sanitize(str(resp))
        return {"answer": text, "mode": "rag_with_llm"}
    except Exception as e:
        try:
            retriever = get_retriever(top_k=body.top_k)
            nodes = retriever.retrieve(body.q)
            snippets = []
            for n in nodes:
                meta = getattr(n.node, "metadata", {}) or {}
                snippets.append({
                    "score": getattr(n, "score", None),
                    "text": n.node.get_content()[:600],
                    "source": meta.get("source"),
                    "id": n.node.node_id,
                })
            return {"answer": None, "mode": "retrieval_only", "reason": _public_error(e), "snippets": snippets}
        except Exception as e2:
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": _public_error(e), "fallback_error": _public_error(e2)})

# ---------- Ask (RAG manual + LLM controlado, sin streaming) ----------
class AskBody(BaseModel):
    q: str
    top_k: int | None = None

from .persona_config import SYSTEM_PROMPT
_PROMPT = SYSTEM_PROMPT + """

Contexto recuperado (puede estar vacío):
{context}

Pregunta de David:
{question}

Responde siguiendo las reglas anteriores. Si el contexto no es suficiente,
dilo explícitamente y propone los siguientes pasos realistas.

Respuesta:
"""


@app.post("/ask")
def ask(body: AskBody):
    try:
        # Recupera contexto (no invoca LLM aún)
        ctx = retrieve_context_for_guardrail(body.q, top_k=body.top_k)

        # 🛡 Guardrail: si no hay contexto útil, no inventar
        if not ctx or max((c["score"] or 0) for c in ctx) < 0.3:
            msg = (
                "No encontré contexto útil en tus documentos para esta pregunta. "
                "Revisa si el tema está cargado en los PDFs o explícame con más detalle "
                "qué información estás buscando."
            )
            final = _sanitize(msg)
            return {"text": final, "chunks": _split_for_telegram(final)}

        context_text = "\n\n---\n\n".join([c["text"] for c in ctx]) if ctx else ""
        prompt = _PROMPT.format(
            question=body.q.strip(),
            context=context_text.strip(),
        )

        # LLM NO-STREAM (complete) -> evita palabras cortadas
        raw = call_llm(prompt)
        final = _sanitize(raw)
        return {"text": final, "chunks": _split_for_telegram(final)}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"error": _public_error(e)},
        )


# ---------- Search (solo retrieval) ----------
class SearchBody(BaseModel):
    q: str
    top_k: int | None = None

@app.post("/search")
def search(body: SearchBody):
    try:
        retriever = get_retriever(top_k=body.top_k)
        nodes = retriever.retrieve(body.q)
        out = []
        for n in nodes:
            meta = getattr(n.node, "metadata", {}) or {}
            out.append({
                "score": getattr(n, "score", None),
                "text": n.node.get_content()[:500],
                "source": meta.get("source"),
                "id": n.node.node_id,
            })
        return {"results": out}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": _public_error(e)})
