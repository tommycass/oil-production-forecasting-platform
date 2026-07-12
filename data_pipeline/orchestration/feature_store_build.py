"""Materialización del feature store (Fase 3, Rol 2) — handoff de Rol 1.

Reproduce **exactamente** las features del entrenamiento **reusando el código de `ml/`**
(única fuente de verdad → cero training-serving skew): la ingeniería sale de
`ml.features.add_engineered_features` y las columnas base/categóricas de `ml.dataset`. Se
materializa **el set de ganancia positiva de la selección** (`ml.features.selected_features`,
ADR-041): las features que entrena el modelo (27 en petróleo, 19 en gas) — el núcleo
autorregresivo del target + las anclas estáticas/categóricas del cold-start —, recursion-safe
por construcción (ADR-042). Es la misma lista que usa `ml/train.py`, así que store y modelo
no pueden divergir.
La única lógica propia es el ensamblado (lectura de Bronze, universo, drop de negativos, merge
del target) que replica `ml.dataset.build_basic_dataset`, con una diferencia: el target se
hace por **left-join** para conservar la última fila de cada pozo (con `y_next` NULL) y que
la API pueda inferir el mes siguiente.

**Dos modelos (ADR-039):** se materializa **una tabla por target** — petróleo
(`prod_pet`) en `feat_produccion_pozo_mensual` (nombre histórico) y gas (`prod_gas`)
en `feat_produccion_pozo_mensual_gas`. Cada tabla tiene su **universo** (pozos con ese
target > 0 en train), sus **features autorregresivas** sobre el target (`prod_pet_*` vs
`prod_gas_*`) y su `y_next`. Las 5 anclas estáticas se duplican entre tablas.
Rol 3 lee la tabla del target que sirve.

Fuente: `bronze.produccion` (crudo, mismas columnas que el CSV que usa el training;
gobernado en el DW). Ver docs/feature-store.md (contrato) y ADR-035 (Revisión).

No importa dagster: la lógica es testeable aislada (paridad vs `build_basic_dataset`).
"""

from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import create_engine

from ml import features as ml_features
from ml.config import BACKFILL_TABLE_SUFFIX, TARGET, TARGETS, retrain_asof, split_bounds
from ml.dataset import BASIC_CATEGORICAL_FEATURES, BASIC_NUMERIC_FEATURES

FEATURE_SCHEMA = "features"
FEATURE_TABLE = "feat_produccion_pozo_mensual"  # petróleo (nombre histórico)

# Columnas crudas a leer de Bronze: claves + las features base (numéricas + categóricas).
# `mes` está en BASIC_NUMERIC_FEATURES; se dedup con dict.fromkeys. Las mismas columnas
# crudas sirven a los dos targets (prod_pet y prod_gas están ambas en las numéricas base).
_RAW_COLS = list(dict.fromkeys(["idpozo", "anio", "mes", *BASIC_NUMERIC_FEATURES, *BASIC_CATEGORICAL_FEATURES]))

# Columnas de rectificación (no son features): se leen para conservar el registro vigente de
# cada (idpozo, mes) —criterio de `silver_produccion_vigente`— y se descartan tras el dedup.
_DEDUP_COLS = ["rectificado", "fecha_data", "fecha_ingesta"]


def table_for(target: str) -> str:
    """Tabla del store para un ``target``. Petróleo mantiene el nombre histórico
    (`feat_produccion_pozo_mensual`); gas (y futuros targets) llevan sufijo, siguiendo
    la convención `_gas` del pipeline de `ml/` (ADR-039): `feat_produccion_pozo_mensual_gas`.
    """
    return FEATURE_TABLE if target == "prod_pet" else f"{FEATURE_TABLE}_{target.removeprefix('prod_')}"


def engine_from_env():
    url = (
        f"postgresql+psycopg2://{os.getenv('POSTGRES_USER', 'oil')}:"
        f"{os.getenv('POSTGRES_PASSWORD', 'oil')}@{os.getenv('POSTGRES_HOST', 'localhost')}:"
        f"{os.getenv('POSTGRES_PORT', '5432')}/{os.getenv('POSTGRES_DB', 'oil_dw')}"
    )
    return create_engine(url)


def leer_bronze(engine) -> pd.DataFrame:
    """Lee las columnas crudas de `bronze.produccion` (todo texto) + las de rectificación
    (para el dedup del vigente)."""
    sel = ", ".join(f'"{c}"' for c in _RAW_COLS + _DEDUP_COLS)
    return pd.read_sql(f"select {sel} from bronze.produccion", engine)


def _dedup_vigente(df: pd.DataFrame) -> pd.DataFrame:
    """Conserva **un** registro por (idpozo, periodo): el **vigente**, con el mismo criterio
    que `silver_produccion_vigente` (rectificado=true, luego última `fecha_data`, luego última
    `fecha_ingesta`). Descarta las columnas de rectificación al terminar. Es **no-op** si no
    están esas columnas (paneles de test) o si no hay duplicados (caso normal de Bronze)."""
    tiene_cols = set(_DEDUP_COLS).issubset(df.columns)
    if tiene_cols and df.duplicated(["idpozo", "periodo"]).any():
        d = df.copy()
        d["_rect"] = d["rectificado"].astype(str).str.strip().str.lower().isin(["t", "true", "1"])
        d["_fdata"] = pd.to_datetime(d["fecha_data"], errors="coerce")
        d["_fing"] = pd.to_datetime(d["fecha_ingesta"], errors="coerce")
        df = (
            d.sort_values(
                ["idpozo", "periodo", "_rect", "_fdata", "_fing"],
                ascending=[True, True, False, False, False],
            )
            .drop_duplicates(["idpozo", "periodo"], keep="first")
            .drop(columns=["_rect", "_fdata", "_fing"])
        )
    return df.drop(columns=[c for c in _DEDUP_COLS if c in df.columns])


def max_periodo_bronze(engine) -> pd.Timestamp | None:
    """Último período observado en Bronze, o ``None`` si está vacío. Sirve para saber si
    un reproceso `asof` **truncaría** datos (backfill histórico)."""
    val = pd.read_sql(
        "select max(make_date(anio::int, mes::int, 1)) as m from bronze.produccion", engine
    )["m"].iloc[0]
    return pd.Timestamp(val) if pd.notna(val) else None


def es_backfill(engine, asof) -> bool:
    """``True`` si el reproceso ``asof`` cae **antes** del último período observado, o sea
    truncaría el store (ADR-040). Un backfill así se materializa en tablas separadas para no
    pisar las que sirve la API; una corrida normal (``asof`` >= último período) escribe en vivo."""
    max_p = max_periodo_bronze(engine)
    return max_p is not None and pd.Timestamp(asof) < max_p


def drop_backfill_tables(engine, targets=TARGETS) -> None:
    """Borra las tablas temporales del reproceso histórico (``..._backfill``)."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        for target in targets:
            tabla = table_for(target) + BACKFILL_TABLE_SUFFIX
            cur.execute(f'drop table if exists {FEATURE_SCHEMA}."{tabla}"')
        raw.commit()
    finally:
        raw.close()


def reescribir_tabla(engine, df: pd.DataFrame, tabla: str, intentos: int = 4) -> None:
    """Reescribe ``features.<tabla>`` con ``df``: drop explícito por psycopg2 + ``to_sql`` sobre
    una ``Connection`` fresca con ``if_exists="append"`` (crea la tabla de cero), evitando el drop
    interno del ``replace``.
    ``to_sql`` con este pandas+sqlalchemy2 tira ``immutabledict is not a sequence`` de forma
    intermitente (estado del pool, no del dato). Como cada intento dropea antes de escribir,
    reintentar es idempotente; en el retry se descarta el pool para forzar una conexión limpia."""
    for intento in range(intentos):
        raw = engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute(f"create schema if not exists {FEATURE_SCHEMA}")
            cur.execute(f'drop table if exists {FEATURE_SCHEMA}."{tabla}"')
            raw.commit()
        finally:
            raw.close()
        try:
            with engine.begin() as conn:
                df.to_sql(tabla, conn, schema=FEATURE_SCHEMA, if_exists="append", index=False)
            return
        except TypeError as exc:
            if "immutabledict" in str(exc) and intento < intentos - 1:
                engine.dispose()  # conexión fresca en el próximo intento (evita el pool degradado)
                continue
            raise


def build_store_features(df: pd.DataFrame, target: str = TARGET, asof=None) -> pd.DataFrame:
    """Construye la tabla de features del store a partir del crudo de producción.

    Replica el ensamblado de `ml.dataset.build_basic_dataset` (tipado, recorte por
    fecha, universo train-only, drop de negativos, merge por calendario) reusando
    `ml.features.add_engineered_features` para las features de ingeniería.
    Target por **left-join** (conserva la última fila de cada pozo, `y_next` NULL)
    para habilitar la inferencia del mes siguiente.

    ``target`` (``prod_pet`` por defecto / ``prod_gas``, ADR-039) cambia el **universo**
    (pozos con ese target > 0 en train), las **features autorregresivas** del set final
    (`{target}_roll3/ratio1/acum12/...`, ADR-041) y el `y_next` (= target del mes t+1).
    Las anclas estáticas no dependen del target. No se llama a `df.copy()` defensivo
    más de lo necesario para poder reusar el mismo crudo de Bronze en los dos targets
    sin recargar.

    ``asof`` recorta el crudo a ``periodo <= asof`` (reproceso "como si fuera el día X",
    ADR-040), **antes** de definir universo y features, para que un reentreno de una fecha
    pasada no use datos posteriores (anti-leakage del backfill). Si es ``None`` se toma de
    la env var ``RETRAIN_ASOF`` (``ml.config.retrain_asof``); si tampoco está, no recorta.
    En una corrida normal (``asof`` = hoy) no recorta nada.
    """
    df = df.copy()
    # idpozo como entero (en Bronze viene texto): clave del store y del lookup de la API.
    df["idpozo"] = pd.to_numeric(df["idpozo"], errors="coerce").astype("int64")
    for c in BASIC_NUMERIC_FEATURES:  # incluye `mes`
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce")  # solo para construir periodo
    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    # dedup del vigente por (idpozo, periodo) —igual criterio que Silver— antes de todo lo
    # demás, para no entrenar/servir con una fila rectificada no vigente. Descarta las columnas
    # de rectificación. No-op si no hay duplicados (Bronze hoy no los tiene) o en paneles de test.
    df = _dedup_vigente(df)

    # reproceso por fecha (ADR-040): recortar a periodo <= asof ANTES de definir el
    # universo y las features, para no usar datos posteriores al reentrenar una fecha
    # pasada (anti-leakage del backfill). asof explícito > env var RETRAIN_ASOF. El
    # entrenamiento lee de esta tabla ya recortada (no recorta de nuevo).
    asof = retrain_asof() if asof is None else pd.Timestamp(asof)
    if asof is not None:
        df = df[df.periodo <= asof].reset_index(drop=True)

    # train_end derivado del anchor (última fecha tras el recorte asof, ADR-028). El store
    # conserva esa fila (y_next NULL), así el training deriva el mismo anchor y queda alineado.
    train_end, _ = split_bounds(df["periodo"].max())

    # universo train-only (anti-leakage de selección, ADR-031): pozos con `target`>0
    # en algún mes <= train_end. El universo gasífero es distinto (y más amplio) que el
    # petrolero (ADR-039).
    pozos = df.loc[(df[target] > 0) & (df.periodo <= train_end), "idpozo"].unique()
    df = df[df.idpozo.isin(pozos)].sort_values(["idpozo", "periodo"]).reset_index(drop=True)

    # descartar producción negativa (errores de dato, ADR-038) antes del feature eng.
    prod_cols = ["prod_pet", "prod_gas", "prod_agua"]
    df = df[~(df[prod_cols] < 0).any(axis=1)].reset_index(drop=True)

    # features de ingeniería sobre el target: REUSO del código de Rol 1 (paridad garantizada).
    df = ml_features.add_engineered_features(df, target=target)

    # target = `target` del mes siguiente, alineado por calendario. LEFT para conservar
    # la última fila de cada pozo (y_next NULL) → la API puede predecir el mes siguiente.
    nxt = df[["idpozo", "periodo", target]].rename(columns={target: "y_next"})
    nxt["periodo"] = nxt["periodo"] - pd.DateOffset(months=1)
    out = df.merge(nxt, on=["idpozo", "periodo"], how="left")

    out["periodo_objetivo"] = out["periodo"] + pd.DateOffset(months=1)

    # `mes` = mes del MES OBJETIVO (t+1), NO el de las medidas: idéntico a
    # `ml.dataset.build_basic_dataset` (paridad training-serving del feature `mes`,
    # ADR-041). El motor de forecast lo re-setea igual en cada paso (ml/forecast.py),
    # así que el serving no depende de este valor; se materializa consistente igual
    # para que el store no contradiga el contrato ni un lector directo del `mes`.
    out["mes"] = out["periodo_objetivo"].dt.month

    # `add_prod_vecinos_mean` (ml/features) castea idpozo a object al hacer merge; lo
    # devolvemos a int64 para que el store tenga una clave limpia (lookup de la API).
    out["idpozo"] = out["idpozo"].astype("int64")

    # Materializar SOLO el set de ganancia positiva de la selección (ADR-041): las
    # features que entrena el modelo (27 petróleo / 19 gas, `ml.features.selected_features`
    # — la misma lista que usa `ml/train.py`, fuente única de verdad). Lo demás que calcula
    # `add_engineered_features` (importancia ≈ 0 / negativa en el ranking) no se persiste.
    cols = ["idpozo", "periodo", "periodo_objetivo"] + ml_features.selected_features(target) + ["y_next"]
    return out[cols].sort_values(["idpozo", "periodo"]).reset_index(drop=True)


def materializar(engine, target: str = TARGET, asof=None, suffix: str = "") -> int:
    """Construye y escribe la tabla del store de un ``target`` en Postgres.
    Devuelve filas escritas. ``asof`` recorta a `periodo <= asof` (reproceso por fecha,
    ADR-040); ``suffix`` escribe en una tabla separada (backfill histórico, no pisa el
    store en vivo). Para los dos modelos, ver `materializar_todos`."""
    df = build_store_features(leer_bronze(engine), target=target, asof=asof)
    reescribir_tabla(engine, df, table_for(target) + suffix)
    return len(df)


def materializar_todos(engine, targets=TARGETS, asof=None, suffix: str = "") -> dict[str, int]:
    """Materializa **una tabla por target** (petróleo + gas, ADR-039).

    Lee Bronze **una sola vez** (las columnas crudas son las mismas para ambos) y
    reescribe cada tabla con su universo/ingeniería/`y_next`. Devuelve `{target: filas}`.
    Es lo que invoca el job de retrain (`features_refrescadas`, ADR-040).

    ``asof`` recorta el crudo a `periodo <= asof` (reproceso "como si fuera el día X",
    ADR-040) para los dos targets; ``None`` toma la env var `RETRAIN_ASOF` o no recorta.
    ``suffix`` (p. ej. ``_backfill``) escribe en tablas separadas: lo usa el retrain para
    aislar un reproceso histórico y **no pisar** el store que sirve la API en vivo.
    """
    raw = leer_bronze(engine)
    filas: dict[str, int] = {}
    for target in targets:
        df = build_store_features(raw, target=target, asof=asof)
        reescribir_tabla(engine, df, table_for(target) + suffix)
        filas[target] = len(df)
    return filas
