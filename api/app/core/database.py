"""Conexión al Data Warehouse (PostgreSQL) para servir datos reales a la API.

La API consulta la capa Gold del DW (esquema `gold`). Las credenciales se leen del
entorno (`POSTGRES_*`, en `api/.env`, gitignoreado). En local apunta a un Postgres de
desarrollo; en cada EC2 apunta al RDS correspondiente (staging/prod) cambiando solo
`POSTGRES_HOST` y `POSTGRES_DB`.

El engine es perezoso (lazy): se crea en el primer uso, no al importar el módulo. Así,
importar `database` no exige tener las `POSTGRES_*` ni una base viva (p. ej. en tests
que no tocan la DB).
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

_engine: Engine | None = None


def _build_url() -> str:
    """Arma la URL de conexión desde las variables de entorno POSTGRES_*."""
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    host = os.environ["POSTGRES_HOST"]
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ["POSTGRES_DB"]
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def get_engine() -> Engine:
    """Devuelve el engine SQLAlchemy, creándolo en el primer uso (singleton perezoso)."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            _build_url(),
            pool_pre_ping=True,  # descarta conexiones muertas antes de usarlas
            poolclass=NullPool,  # API síncrona: una conexión por request, sin pool compartido
        )
    return _engine


def fetch_all(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Ejecuta un SELECT y devuelve las filas como lista de dicts.

    Usa parámetros nombrados (`:nombre`) en el SQL para evitar inyección.
    """
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row) for row in result.mappings()]
