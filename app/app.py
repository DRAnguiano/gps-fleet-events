from fastapi import FastAPI, Query, Header
from fastapi.responses import JSONResponse, ORJSONResponse
from pydantic import BaseModel

from .indexer import (
    build_index,
    get_retriever,
    retrieve_context_for_guardrail,
    call_llm,
    _to_int,
)

from . import fleet_intent
from . import fleet_queries

from .persona_config import SYSTEM_PROMPT
from .settings import (
    REINDEX_API_KEY,
    INCLUDE_ERROR_DETAILS,
)

import os
import sys
import time
import traceback

app = FastAPI(
    default_response_class=ORJSONResponse
)

# =========================================================
# UTILS
# =========================================================

def _sanitize(s: str):
    if not s:
        return s

    s = s.replace("\uFFFD", "")

    return (
        s.encode("utf-8", "ignore")
        .decode("utf-8")
        .strip()
    )

def _split_for_telegram(
    text: str,
    limit: int = 3500
):
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

def _public_error(exc: Exception):
    if INCLUDE_ERROR_DETAILS:
        return f"{type(exc).__name__}: {exc}"

    return "internal_error"

# =========================================================
# HEALTH
# =========================================================

@app.get("/health")
def health():
    return {"status": "ok"}

# =========================================================
# ESTATUS DE FLOTA
#
# Acceso directo para n8n y para tableros: la misma respuesta
# que da /ask, pero sin pasar por la detección de intención.
# =========================================================

@app.get("/fleet/status")
def fleet_status(
    unit: str | None = Query(default=None),
):
    if not fleet_queries.is_enabled():
        return JSONResponse(
            status_code=503,
            content={"error": "fleet_db_unavailable"},
        )

    try:
        if unit:
            resolved = fleet_queries.resolve_unit(unit)


            if not resolved:
                return JSONResponse(
                    status_code=404,
                    content={"error": "unit_not_found", "unit": unit},
                )

            return {
                "unit": resolved,
                "status": fleet_queries.unit_status(resolved),
            }

        return {
            "available": fleet_queries.available_units(),
            "without_signal": fleet_queries.units_without_signal(),
            "long_stops": fleet_queries.long_stops(),
        }

    except fleet_queries.FleetUnavailable as e:
        print(f"[fleet] Base no disponible: {e}")

        return JSONResponse(
            status_code=503,
            content={"error": "fleet_db_unavailable"},
        )

    except Exception as e:
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={"error": _public_error(e)},
        )

# =========================================================
# REINDEX
# =========================================================

class ReindexBody(BaseModel):
    top_k: int | None = None

@app.post("/reindex")
def reindex(
    body: ReindexBody | None = None,
    k: int | None = Query(default=None),
    x_api_key: str | None = Header(default=None),
):
    if (
        REINDEX_API_KEY
        and x_api_key != REINDEX_API_KEY
    ):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized"}
        )

    t0 = time.time()

    top_k = _to_int(
        k,
        _to_int(
            getattr(body, "top_k", None),
            3
        )
    )

    try:
        build_index()

        return {
            "status": "ok",
            "top_k": top_k,
            "elapsed_s": round(
                time.time() - t0,
                2
            ),
        }

    except Exception as e:
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": _public_error(e),
            },
        )

# =========================================================
# ASK
# =========================================================

class AskBody(BaseModel):
    q: str
    top_k: int | None = None

RAG_PROMPT = """

=== CONTEXTO RECUPERADO ===
{context}
=== FIN DEL CONTEXTO ===

=== MENSAJE DEL USUARIO ===
{question}

INSTRUCCIONES:
- Usa únicamente información textual presente en el contexto.
- No inventes procesos ni políticas.
- No inventes unidades, operadores, ubicaciones, litros, kilómetros ni horas.
- Cuando el contexto traiga unidad y hora, inclúyelas en la respuesta.
- Si el contexto no contiene la respuesta, dilo claramente.
- Responde breve y profesional.

RESPUESTA:
"""

@app.post("/ask")
def ask(body: AskBody):

    try:

        question = body.q.strip()

        # =====================================================
        # CONSULTA OPERATIVA (gps_event)
        #
        # Va primero porque tiene la respuesta exacta: "¿dónde
        # está la T-142?" se contesta con la base de eventos, no
        # con un PDF. Si no aplica, cae al RAG documental.
        # =====================================================

        try:
            operativa = fleet_intent.try_answer(question)
        except Exception as e:
            # La base operativa es un extra: si falla, el bot sigue
            # respondiendo sobre documentos en vez de caerse.
            traceback.print_exc()
            operativa = None
            print(f"[fleet] Consulta operativa fallida: {type(e).__name__}: {e}")

        if operativa:
            final = _sanitize(operativa["text"])

            return {
                "text": final,
                "chunks": _split_for_telegram(final),
                "mode": "fleet",
                "intent": operativa["intent"],
                "unit": operativa.get("matched_unit"),
            }

        # =====================================================
        # RAG DOCUMENTAL
        #
        # Toda consulta pasa por recuperación. No hay atajos que
        # respondan sin contexto: un dato operativo inventado
        # deriva en una mala asignación de viaje.
        # =====================================================

        ctx = retrieve_context_for_guardrail(
            question,
            top_k=body.top_k,
        )

        if (
            not ctx
            or max(
                (c["score"] or 0)
                for c in ctx
            ) < 0.60
        ):

            # Mismo texto que el SYSTEM_PROMPT indica al modelo:
            # la respuesta es idéntica venga del guardarraíl o del LLM.
            msg = (
                "No tengo ese dato disponible "
                "en este momento."
            )

            final = _sanitize(msg)

            return {
                "text": final,
                "chunks": _split_for_telegram(final),
                "mode": "fallback",
            }

        context_text = "\n\n---\n\n".join(
            [c["text"] for c in ctx]
        )

        prompt = (
            SYSTEM_PROMPT
            + RAG_PROMPT.format(
                context=context_text,
                question=question,
            )
        )

        raw = call_llm(prompt)

        final = _sanitize(raw)

        return {
            "text": final,
            "chunks": _split_for_telegram(final),
            "mode": "rag",
        }

    except Exception as e:

        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "error": _public_error(e)
            },
        )

# =========================================================
# SEARCH
# =========================================================

class SearchBody(BaseModel):
    q: str
    top_k: int | None = None

@app.post("/search")
def search(body: SearchBody):

    try:

        retriever = get_retriever(
            top_k=body.top_k
        )

        nodes = retriever.retrieve(body.q)

        out = []

        for n in nodes:

            meta = getattr(
                n.node,
                "metadata",
                {}
            ) or {}

            out.append({
                "score": getattr(
                    n,
                    "score",
                    None
                ),
                "text": n.node.get_content()[:500],
                "source": meta.get("source"),
                "id": n.node.node_id,
            })

        return {"results": out}

    except Exception as e:

        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={
                "error": _public_error(e)
            },
        )