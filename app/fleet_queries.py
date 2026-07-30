"""
Acceso a la base operativa (gps_event y sus vistas).

Regla del módulo: **el LLM nunca genera SQL**. Aquí viven consultas fijas,
parametrizadas, y lo único que el modelo aporta es la redacción final sobre
datos ya recuperados. Un LLM escribiendo SQL contra la base de producción
puede filtrar tablas enteras o tumbar el servidor con un cross join; además,
una consulta mal formada devolvería datos plausibles pero incorrectos, que es
justo lo que no puede pasar con una decisión de asignación de viaje.

Las reglas de negocio (qué es "en movimiento", qué es "en base") no se
reimplementan aquí: viven en las vistas de sql/02_vistas_operativas.sql, para
que el bot, Power BI y las consultas manuales respondan lo mismo.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .settings import (
    DATABASE_URL,
    FLEET_QUERY_TIMEOUT_S,
    FLEET_TZ,
)

_POOL = None
_POOL_FAILED = False


class FleetUnavailable(RuntimeError):
    """La base operativa no está accesible ahora mismo (no es un error de la consulta)."""


def is_enabled() -> bool:
    """La capa operativa es opcional: sin DATABASE_URL, /ask solo usa documentos."""
    return bool(DATABASE_URL) and not _POOL_FAILED


def _get_pool():
    """Pool perezoso: no se conecta hasta la primera consulta de flota."""
    global _POOL, _POOL_FAILED

    if _POOL is not None:
        return _POOL

    try:
        from psycopg_pool import ConnectionPool

        _POOL = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=4,
            timeout=FLEET_QUERY_TIMEOUT_S,
            kwargs={"options": f"-c statement_timeout={int(FLEET_QUERY_TIMEOUT_S * 1000)}"},
            open=True,
        )
        print("[fleet] Pool de Postgres abierto.")
    except Exception as e:
        # Sin base, el bot sigue respondiendo sobre documentos.
        _POOL_FAILED = True
        print(f"[fleet] No se pudo abrir el pool: {type(e).__name__}: {e}")
        raise

    return _POOL


def _rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import PoolTimeout

    try:
        pool = _get_pool()

        with pool.connection() as conn:
            conn.execute(f"SET LOCAL TIME ZONE '{FLEET_TZ}'")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())

    except (PoolTimeout, psycopg.OperationalError) as e:
        # Base inalcanzable: es indisponibilidad del servicio, no una consulta
        # inválida. Se distingue para responder 503 en vez de 500.
        raise FleetUnavailable(str(e)) from e


# ---------------------------------------------------------------------------
# Resolución de unidad
# ---------------------------------------------------------------------------

def _normalize_code(code: str) -> str:
    """T-142, t142 y 'T 142' son la misma unidad para quien pregunta."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def resolve_unit(code: str) -> Optional[str]:
    """
    Traduce lo que escribió el usuario al unit_code real de la base.

    Se hace contra la base y no con una lista fija porque el proveedor nombra
    las unidades de formas distintas (T-142, V MOVIL 4) y ese catálogo cambia.
    """
    norm = _normalize_code(code)
    if not norm:
        return None

    rows = _rows(
        """
        SELECT unit_code, MAX(event_time) AS ultimo
        FROM gps_event
        WHERE regexp_replace(upper(unit_code), '[^A-Z0-9]', '', 'g') = %s
        GROUP BY unit_code
        ORDER BY ultimo DESC
        LIMIT 1
        """,
        (norm,),
    )

    return rows[0]["unit_code"] if rows else None


# ---------------------------------------------------------------------------
# Consultas
# ---------------------------------------------------------------------------

def unit_status(unit_code: str) -> Optional[Dict[str, Any]]:
    """Estatus consolidado de una unidad, tal como lo define la vista."""
    rows = _rows(
        """
        SELECT unit_code, estatus, lectura_reciente, horas_sin_reporte,
               ultimo_evento_at, ultimo_movimiento_at,
               horas_sin_moverse, detencion_larga, geofence_name,
               geofence_kind, speed_kmh
        FROM v_unidad_estatus
        WHERE unit_code = %s
        """,
        (unit_code,),
    )
    return rows[0] if rows else None


def available_units(limit: int = 20) -> List[Dict[str, Any]]:
    """
    Unidades sobre las que Tráfico puede contar ahora.

    EN_BASE primero (disponible de inmediato), luego EN_ZONA_LAGUNA (el
    operador la tiene en casa: disponible, pero hay que avisarle). Las paradas
    en ruta quedan fuera a propósito: no son disponibilidad.
    """
    return _rows(
        """
        SELECT unit_code, estatus, horas_sin_moverse, geofence_name,
               ultimo_evento_at, lectura_reciente
        FROM v_unidad_estatus
        WHERE estatus IN ('EN_BASE', 'EN_ZONA_LAGUNA')
        ORDER BY
          CASE estatus WHEN 'EN_BASE' THEN 1 ELSE 2 END,
          horas_sin_moverse DESC
        LIMIT %s
        """,
        (limit,),
    )


def units_without_signal(limit: int = 20) -> List[Dict[str, Any]]:
    return _rows(
        """
        SELECT unit_code, estatus, ultimo_evento_at, horas_sin_moverse,
               horas_sin_reporte, geofence_name
        FROM v_unidad_estatus
        WHERE estatus = 'SIN_SENAL'
           OR NOT lectura_reciente
        ORDER BY ultimo_evento_at
        LIMIT %s
        """,
        (limit,),
    )


def long_stops(limit: int = 20) -> List[Dict[str, Any]]:
    """Detenidas fuera de zona conocida por más tiempo del razonable."""
    return _rows(
        """
        SELECT unit_code, estatus, horas_sin_moverse, geofence_name,
               ultimo_movimiento_at, ultimo_evento_at, lectura_reciente
        FROM v_unidad_estatus
        WHERE detencion_larga
          AND estatus = 'PARADA_EN_RUTA'
        ORDER BY horas_sin_moverse DESC
        LIMIT %s
        """,
        (limit,),
    )


def fuel_summary(unit_code: str, days: int = 7) -> List[Dict[str, Any]]:
    return _rows(
        """
        SELECT dia, litros_cargados, litros_descargados,
               eventos_llenado, eventos_descarga,
               odometro_max_km, odometro_min_km
        FROM v_combustible_diario
        WHERE unit_code = %s
          AND dia >= (now() AT TIME ZONE %s)::date - %s::int
        ORDER BY dia DESC
        """,
        (unit_code, FLEET_TZ, days),
    )


def recent_events(unit_code: str, limit: int = 10) -> List[Dict[str, Any]]:
    return _rows(
        """
        SELECT event_time, type, geofence_name, geofence_kind, speed_kmh,
               data->>'fuel_liters'   AS fuel_liters,
               data->>'odometer_km'   AS odometer_km
        FROM gps_event
        WHERE unit_code = %s
        ORDER BY event_time DESC
        LIMIT %s
        """,
        (unit_code, limit),
    )


def ingest_health() -> Dict[str, Any]:
    """
    Última señal recibida por el sistema completo.

    Sirve para responder honestamente: si hace horas que no entra nada, el
    problema puede estar en la ingesta y no en la flota.
    """
    rows = _rows(
        """
        SELECT MAX(event_time)  AS ultimo_evento,
               MAX(created_at)  AS ultima_carga,
               COUNT(*)         AS eventos_24h
        FROM gps_event
        WHERE created_at >= now() - INTERVAL '24 hours'
        """
    )
    return rows[0] if rows else {}
