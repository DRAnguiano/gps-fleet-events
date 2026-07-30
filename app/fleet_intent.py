"""
Router de consultas operativas para /ask.

Decide si una pregunta se responde con la base de eventos (gps_event) en lugar
de con los documentos indexados, y arma la respuesta.

Dos decisiones de diseño que conviene entender antes de tocar este archivo:

1. **La intención se detecta con reglas, no con el LLM.** Son siete formas de
   preguntar sobre una flota, no un problema de comprensión abierto. Reglas
   explícitas fallan de manera predecible y se corrigen agregando una palabra;
   un clasificador con LLM agrega latencia y falla de formas que nadie puede
   reproducir.

2. **La respuesta operativa se arma con plantillas, no se le pide al modelo
   que la redacte.** Los datos ya vienen exactos de la base; pasarlos por un
   LLM solo agrega la posibilidad de que cambie una hora o invente una
   ubicación. El modelo sigue redactando para las consultas documentales, que
   es donde aporta.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import fleet_queries as fq
from .settings import FLEET_STALE_HOURS, FLEET_TZ

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(FLEET_TZ)
except Exception:
    _TZ = timezone.utc


# ---------------------------------------------------------------------------
# Detección de intención
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return s


# El orden importa: lo más específico primero. "¿qué unidades están sin señal?"
# menciona unidades y señal; sin este orden caería en disponibilidad.
_INTENTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("HEALTH", (
        "esta entrando informacion", "esta llegando informacion",
        "ultima señal", "ultima senal", "esta funcionando el sistema",
        "hay datos nuevos", "esta actualizada la base",
    )),
    ("NO_SIGNAL", (
        "sin señal", "sin senal", "sin reportar", "no reporta", "no reportan",
        "perdio conexion", "perdieron conexion", "sin conexion",
        "sin lectura", "no ha reportado",
    )),
    ("LONG_STOPS", (
        "mucho tiempo parada", "mucho tiempo paradas", "detenidas mucho",
        "paradas en ruta", "parada en ruta", "atoradas", "atorada",
        "detencion larga", "llevan paradas", "tiempo parada",
    )),
    ("FUEL", (
        "combustible", "diesel", "litros", "cargo", "cargaron", "llenado",
        "descarga", "rendimiento",
    )),
    ("EVENTS", (
        "ultimos eventos", "ultimo evento", "que ha hecho", "actividad",
        "historial", "que paso con",
    )),
    ("AVAILABLE", (
        "disponibles", "disponible", "libres", "libre", "que unidades puedo",
        "quien puede salir", "cuales pueden salir", "listas para salir",
        "en patio", "en base",
    )),
    ("STATUS", (
        "estatus", "status", "estado", "donde esta", "donde anda",
        "como esta", "ya se movio", "se esta moviendo", "sigue parada",
        "ubicacion",
    )),
]

# Formas en que Tráfico nombra una unidad: T-142, T142, V88, "V MOVIL 4",
# "unidad 142", "la 142".
_UNIT_PATTERNS = (
    r"\b([TV]\s*-?\s*\d{1,4})\b",
    r"\b(v\s*movil\s*\d{1,3})\b",
    r"\bunidad\s+([a-z0-9\-]{1,12})\b",
    r"\btracto\s+([a-z0-9\-]{1,12})\b",
    r"\bla\s+(\d{2,4})\b",
)


def extract_unit(question: str) -> Optional[str]:
    q = _norm(question)

    for pat in _UNIT_PATTERNS:
        m = re.search(pat, q, re.IGNORECASE)
        if m:
            return m.group(1)

    return None


def detect_intent(question: str) -> Optional[str]:
    q = _norm(question)

    for intent, keywords in _INTENTS:
        if any(k in q for k in keywords):
            return intent

    return None


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------

_ESTATUS_TEXTO = {
    "EN_BASE": "en base o patio (disponible)",
    "EN_ZONA_LAGUNA": "detenida en la zona de La Laguna (probablemente con el operador en su domicilio)",
    "PARADA_EN_RUTA": "detenida en ruta",
    "EN_MOVIMIENTO": "en movimiento",
    "SIN_SENAL": "sin señal (el proveedor reportó pérdida de conexión)",
}


def _hora(dt: Optional[datetime]) -> str:
    if not dt:
        return "sin fecha"

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(_TZ).strftime("%d/%m %H:%M")


def _horas(valor: Any) -> str:
    if valor is None:
        return "?"

    h = float(valor)

    if h < 1:
        return f"{int(h * 60)} min"

    if h < 48:
        return f"{h:.1f} h"

    return f"{h / 24:.1f} días"


def _aviso_dato_viejo(ultimo: Optional[datetime]) -> str:
    """
    El SYSTEM_PROMPT exige advertir cuando el dato es viejo; aquí se cumple
    con el dato en la mano en lugar de confiar en que el modelo lo note.
    """
    if not ultimo:
        return ""

    if ultimo.tzinfo is None:
        ultimo = ultimo.replace(tzinfo=timezone.utc)

    horas = (datetime.now(timezone.utc) - ultimo).total_seconds() / 3600.0

    if horas >= FLEET_STALE_HOURS:
        return f"\n⚠ Último reporte hace {_horas(horas)}; el dato puede no estar actualizado."

    return ""


def _fmt_status(row: Dict[str, Any]) -> str:
    estatus = _ESTATUS_TEXTO.get(row["estatus"], row["estatus"])

    lineas = [
        f"{row['unit_code']}: {estatus}.",
        f"Último reporte: {_hora(row['ultimo_evento_at'])}"
        + (f" cerca de {row['geofence_name']}" if row.get("geofence_name") else "")
        + ".",
        f"Sin moverse desde hace {_horas(row['horas_sin_moverse'])}.",
    ]

    if row.get("detencion_larga") and row["estatus"] == "PARADA_EN_RUTA":
        lineas.append("Detención larga fuera de zona conocida: conviene llamar al operador.")

    return "\n".join(lineas) + _aviso_dato_viejo(row.get("ultimo_evento_at"))


def _fmt_lista(filas: List[Dict[str, Any]], titulo: str, vacio: str) -> str:
    if not filas:
        return vacio

    out = [titulo]

    for r in filas:
        detalle = f"  {r['unit_code']} — {_ESTATUS_TEXTO.get(r['estatus'], r['estatus'])}"

        if r.get("horas_sin_moverse") is not None:
            detalle += f", {_horas(r['horas_sin_moverse'])} sin moverse"

        if r.get("geofence_name"):
            detalle += f" ({r['geofence_name']})"

        detalle += f". Último reporte {_hora(r.get('ultimo_evento_at') or r.get('ultimo_movimiento_at'))}"

        # Sin lectura reciente el estatus sigue siendo el último conocido,
        # pero quien decide tiene que saber que puede haber cambiado.
        if r.get("horas_sin_reporte") is not None:
            detalle += f", sin reportar desde hace {_horas(r['horas_sin_reporte'])}"
        elif r.get("lectura_reciente") is False:
            detalle += " (sin reportar desde entonces)"

        out.append(detalle + ".")

    return "\n".join(out)


def _fmt_fuel(unit: str, filas: List[Dict[str, Any]], dias: int) -> str:
    if not filas:
        return f"{unit}: sin eventos de combustible en los últimos {dias} días."

    out = [f"{unit} — combustible, últimos {dias} días:"]

    for r in filas:
        partes = []

        if r["litros_cargados"]:
            partes.append(f"cargó {r['litros_cargados']:g} l ({r['eventos_llenado']} evento(s))")

        if r["litros_descargados"]:
            partes.append(f"descargó {r['litros_descargados']:g} l ({r['eventos_descarga']} evento(s))")

        if not partes:
            continue

        out.append(f"  {r['dia'].strftime('%d/%m')}: " + "; ".join(partes) + ".")

    if len(out) == 1:
        return f"{unit}: sin cargas ni descargas registradas en los últimos {dias} días."

    return "\n".join(out)


def _fmt_events(unit: str, filas: List[Dict[str, Any]]) -> str:
    if not filas:
        return f"{unit}: sin eventos registrados."

    out = [f"{unit} — últimos eventos:"]

    for r in filas:
        linea = f"  {_hora(r['event_time'])} {r['type']}"

        if r.get("geofence_name"):
            linea += f" — {r['geofence_name']}"

        if r.get("speed_kmh") is not None:
            linea += f" — {float(r['speed_kmh']):g} km/h"

        if r.get("fuel_liters"):
            linea += f" — {r['fuel_liters']} l"

        out.append(linea)

    return "\n".join(out)


def _fmt_health(row: Dict[str, Any]) -> str:
    if not row or not row.get("ultima_carga"):
        return (
            "No ha entrado ningún evento en las últimas 24 horas. "
            "Conviene revisar la ingesta (n8n, la conexión IMAP y el buzón de alertas)."
        )

    return (
        f"Última carga a la base: {_hora(row['ultima_carga'])}.\n"
        f"Evento más reciente: {_hora(row['ultimo_evento'])}.\n"
        f"Eventos en las últimas 24 h: {row['eventos_24h']}."
    )


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

def try_answer(question: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve la respuesta operativa, o None si la pregunta no es de flota
    (o si la base no está disponible) para que /ask siga con el RAG documental.
    """
    if not fq.is_enabled():
        return None

    intent = detect_intent(question)
    if not intent:
        return None

    unidad_texto = extract_unit(question)

    # Estas tres necesitan una unidad concreta.
    if intent in ("STATUS", "FUEL", "EVENTS"):
        if not unidad_texto:
            if intent == "STATUS":
                # "¿qué unidades están libres?" también dispara STATUS.
                intent = "AVAILABLE"
            else:
                return {
                    "text": "¿De qué unidad? Indícame el número, por ejemplo T-142.",
                    "intent": intent,
                    "matched_unit": None,
                }
        else:
            unidad = fq.resolve_unit(unidad_texto)

            if not unidad:
                return {
                    "text": (
                        f"No encuentro la unidad {unidad_texto.upper()} en los eventos registrados. "
                        "Verifica el número."
                    ),
                    "intent": intent,
                    "matched_unit": None,
                }

            if intent == "STATUS":
                row = fq.unit_status(unidad)
                texto = _fmt_status(row) if row else f"{unidad}: sin estatus disponible."
            elif intent == "FUEL":
                texto = _fmt_fuel(unidad, fq.fuel_summary(unidad, days=7), dias=7)
            else:
                texto = _fmt_events(unidad, fq.recent_events(unidad, limit=8))

            return {"text": texto, "intent": intent, "matched_unit": unidad}

    if intent == "AVAILABLE":
        texto = _fmt_lista(
            fq.available_units(),
            "Unidades disponibles:",
            "No hay unidades en base ni en la zona de La Laguna en este momento.",
        )
    elif intent == "NO_SIGNAL":
        texto = _fmt_lista(
            fq.units_without_signal(),
            "Unidades sin señal o sin lectura reciente:",
            "Todas las unidades han reportado recientemente.",
        )
    elif intent == "LONG_STOPS":
        texto = _fmt_lista(
            fq.long_stops(),
            "Unidades detenidas en ruta por tiempo prolongado:",
            "Ninguna unidad lleva una detención larga fuera de zona conocida.",
        )
    else:  # HEALTH
        texto = _fmt_health(fq.ingest_health())

    return {"text": texto, "intent": intent, "matched_unit": None}
