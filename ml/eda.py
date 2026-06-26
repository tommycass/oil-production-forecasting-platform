"""Utilidades para el EDA / feature engineering del forecast (Fase 3).

Este módulo concentra las funciones del análisis exploratorio para que los
notebooks queden limpios (sin lógica definida inline). Es deliberadamente
distinto de ``ml/dataset.py``:

- ``ml/dataset.py`` arma el frame de **modelado** (subset de columnas, universo
  petrolero sobre todo el histórico, features, split de 3 vías).
- ``ml/eda.py`` carga el dataset **crudo completo** (las 40 columnas de origen)
  restringido **solo a train**, para explorar sin mirar val/test.

Las fechas de corte se importan de ``ml/config.py`` (ADR-028) para no
desincronizar el EDA del modelado.

Regla anti-leakage del EDA: todo se mira sobre ``train`` (periodo <= TRAIN_END),
y el universo de pozos petroleros se define **dentro de train**.
"""
from __future__ import annotations

import pandas as pd

from ml.config import DATA_CSV, TARGET, TRAIN_END

# --- Diccionario de columnas crudas --------------------------------------
# Cada entrada: nombre -> (rol, unidad, descripción).
# Fuente del dataset: Secretaría de Energía (data.gob.ar), producción de pozos
# de gas y petróleo (cuencas no convencionales). Grano nativo: (pozo, mes).
RAW_COLUMNS: dict[str, tuple[str, str, str]] = {
    # --- Claves e identificadores ---
    "idpozo":      ("identificador", "—", "ID único del pozo (clave natural del pozo)."),
    "idempresa":   ("identificador", "—", "ID de la empresa operadora (clave natural de la operadora)."),
    "idusuario":   ("metadata",      "—", "ID del usuario que cargó/rectificó el registro."),
    "idareapermisoconcesion": ("identificador", "—", "ID del área de permiso/concesión."),
    "idareayacimiento":       ("identificador", "—", "ID del yacimiento/área (clave natural del yacimiento)."),
    # --- Tiempo ---
    "anio":        ("temporal", "año",  "Año del registro de producción."),
    "mes":         ("temporal", "1-12", "Mes del registro de producción."),
    "fechaingreso":("metadata", "fecha", "Fecha de ingreso del registro al sistema de la Secretaría."),
    "fecha_data":  ("metadata", "fecha", "Fecha/versión del dato (usada para deduplicar rectificaciones)."),
    # --- Medidas de producción (mensuales) ---
    "prod_pet":    ("medida",  "m³",   "Producción de petróleo del mes. >>> TARGET del forecast (t+1)."),
    "prod_gas":    ("medida",  "Mm³",  "Producción de gas del mes (miles de m³)."),
    "prod_agua":   ("medida",  "m³",   "Producción de agua del mes."),
    "tef":         ("medida",  "días", "Tiempo efectivo de producción del mes (días en que el pozo produjo). No aditiva."),
    # --- Medidas de inyección (mensuales) ---
    "iny_agua":    ("medida",  "m³",   "Inyección de agua en el mes (recuperación secundaria)."),
    "iny_gas":     ("medida",  "Mm³",  "Inyección de gas en el mes."),
    "iny_co2":     ("medida",  "—",    "Inyección de CO₂ en el mes."),
    "iny_otro":    ("medida",  "—",    "Inyección de otros fluidos en el mes."),
    # --- Atributos del pozo ---
    "sigla":         ("atributo_pozo", "—",      "Identificador legible / sigla del pozo."),
    "profundidad":   ("atributo_pozo", "metros", "Profundidad del pozo."),
    "vida_util":     ("atributo_pozo", "—",      "Vida útil del pozo (atributo declarado de origen)."),
    "tipoextraccion":("categórica",    "—",      "Método de extracción (p. ej. surgente, bombeo mecánico, electrosumergible)."),
    "tipoestado":    ("categórica",    "—",      "Estado del pozo en el mes (p. ej. produciendo, parado, abandonado)."),
    "tipopozo":      ("categórica",    "—",      "Tipo de pozo: PETROLIFERO / GASIFERO / INYECCION / SUMIDERO."),
    "formprod":      ("categórica",    "—",      "Formación productiva (código de la formación en producción)."),
    "formacion":     ("categórica",    "—",      "Formación geológica del pozo."),
    "clasificacion": ("categórica",    "—",      "Clasificación del pozo: EXPLOTACION / EXPLORACION."),
    "subclasificacion": ("categórica", "—",      "Subclasificación del pozo."),
    "tipo_de_recurso":  ("categórica", "—",      "Tipo de recurso: CONVENCIONAL / NO CONVENCIONAL (TIGHT, SHALE)."),
    "sub_tipo_recurso": ("categórica", "—",      "Subtipo de recurso."),
    "proyecto":      ("categórica",    "—",      "Proyecto asociado al pozo."),
    # --- Operadora / ubicación ---
    "empresa":              ("categórica", "—", "Razón social de la operadora (nombre)."),
    "areapermisoconcesion": ("categórica", "—", "Nombre del área de permiso/concesión."),
    "areayacimiento":       ("categórica", "—", "Nombre del yacimiento / área."),
    "cuenca":               ("categórica", "—", "Cuenca sedimentaria (p. ej. NEUQUINA)."),
    "provincia":            ("categórica", "—", "Provincia."),
    "coordenadax":          ("geografía",  "grados", "Longitud del pozo (grados decimales, ~-68 en cuenca Neuquina)."),
    "coordenaday":          ("geografía",  "grados", "Latitud del pozo (grados decimales, ~-38 en cuenca Neuquina)."),
    # --- Metadata de carga / auditoría ---
    "observaciones": ("metadata", "texto", "Observaciones en texto libre del registro."),
    "rectificado":   ("metadata", "flag",  "Indica si el registro fue rectificado/corregido."),
    "habilitado":    ("metadata", "flag",  "Indica si el registro está habilitado/vigente."),
}

# Columnas numéricas que tienen sentido tratar como continuas en el EDA.
NUMERIC_COLS = [
    "prod_pet", "prod_gas", "prod_agua",
    "iny_agua", "iny_gas", "iny_co2", "iny_otro",
    "tef", "profundidad", "coordenadax", "coordenaday",
]


def load_train_raw(path=DATA_CSV) -> pd.DataFrame:
    """Carga el dataset crudo completo restringido a **train** (anti-leakage).

    Pasos:
    1. Lee las 40 columnas crudas del CSV.
    2. Castea a numérico las medidas continuas.
    3. Construye ``periodo`` (primer día del mes).
    4. Filtra a ``periodo <= TRAIN_END`` (ADR-028).
    5. Restringe al universo petrolero **definido dentro de train**: pozos con
       al menos un mes de ``prod_pet > 0`` en train.

    No mira val ni test en ningún paso.
    """
    df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)

    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["periodo"] = pd.to_datetime(dict(year=df.anio, month=df.mes, day=1))

    # 1) recorte temporal a train
    train = df[df.periodo <= TRAIN_END].copy()

    # 2) universo petrolero calculado SOLO con datos de train
    pozos_petroleros = train.loc[train[TARGET] > 0, "idpozo"].unique()
    train = (
        train[train.idpozo.isin(pozos_petroleros)]
        .sort_values(["idpozo", "periodo"])
        .reset_index(drop=True)
    )
    return train


def data_dictionary() -> pd.DataFrame:
    """Devuelve el diccionario de columnas como tabla (rol, unidad, descripción)."""
    rows = [
        {"columna": col, "rol": rol, "unidad": unidad, "descripción": desc}
        for col, (rol, unidad, desc) in RAW_COLUMNS.items()
    ]
    return pd.DataFrame(rows).set_index("columna")


def null_report(df: pd.DataFrame) -> pd.DataFrame:
    """% de nulls por columna cruda, ordenado de mayor a menor.

    Devuelve una tabla compacta con dtype, conteo y % de nulls. Excluye la
    columna derivada ``periodo`` para reportar solo lo crudo.
    """
    cols = [c for c in df.columns if c != "periodo"]
    n = len(df)
    rep = pd.DataFrame(
        {
            "rol": [RAW_COLUMNS.get(c, ("—",))[0] for c in cols],
            "dtype": [str(df[c].dtype) for c in cols],
            "n_nulls": [int(df[c].isna().sum()) for c in cols],
        },
        index=cols,
    )
    rep["pct_null"] = (rep["n_nulls"] / n * 100).round(2)
    rep.index.name = "columna"
    return rep.sort_values("pct_null", ascending=False)


def null_report_styled(df: pd.DataFrame):
    """Versión estilada del ``null_report`` (barra + color) para el notebook."""
    rep = null_report(df)
    return (
        rep.style
        .format({"pct_null": "{:.2f}%", "n_nulls": "{:,}"})
        .bar(subset=["pct_null"], color="#d65f5f", vmin=0, vmax=100)
        .set_caption("Nulls por columna cruda (train)")
    )
