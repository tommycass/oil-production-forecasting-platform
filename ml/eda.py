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

import math

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ml.config import DATA_CSV, TARGET, split_bounds

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

    # 1) recorte temporal a train (corte derivado de la última fecha observada, ADR-028)
    train_end, _ = split_bounds(df["periodo"].max())
    train = df[df.periodo <= train_end].copy()

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


# --- ¿Qué columnas tiene sentido analizar en el EDA? ----------------------
# Decisión automática a partir de los datos de train. Descartamos lo que no
# aporta a un histograma/frecuencia: identificadores (claves), metadata de
# carga, el eje temporal, columnas casi vacías y columnas (casi) constantes.

# Columnas numéricas que, pese a serlo, son el eje temporal del panel y no
# features del pozo: no se grafican como distribución.
_TEMPORAL_INDEX = {"anio", "mes"}


def _column_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Estadísticos base por columna cruda: rol, dtype, cardinalidad, % null y
    % de la categoría/valor dominante (sobre el total de filas)."""
    cols = [c for c in df.columns if c != "periodo"]
    n = len(df)
    recs = []
    for c in cols:
        s = df[c]
        vc = s.value_counts(dropna=True)
        recs.append(
            {
                "columna": c,
                "rol": RAW_COLUMNS.get(c, ("—",))[0],
                "dtype": str(s.dtype),
                "n_unique": int(s.nunique(dropna=True)),
                "pct_null": round(s.isna().mean() * 100, 2),
                "pct_dominante": round(vc.iloc[0] / n * 100, 1) if len(vc) else 0.0,
            }
        )
    return pd.DataFrame(recs).set_index("columna")


def column_eda_plan(
    df: pd.DataFrame, *, null_thresh: float = 90.0, dominant_thresh: float = 99.0
) -> pd.DataFrame:
    """Clasifica cada columna cruda en ``histograma`` / ``frecuencia`` / ``descartar``.

    Reglas (en orden), pensadas para no graficar lo que no informa:

    - ``identificador`` -> descartar (son claves, no se distribuyen).
    - ``metadata`` -> descartar (fechas de carga, flags de auditoría, usuario).
    - eje temporal (``anio``/``mes``) -> descartar (índice del panel, no feature).
    - ``pct_null >= null_thresh`` -> descartar (casi todo vacío).
    - ``n_unique <= 1`` -> descartar (constante).
    - ``pct_dominante >= dominant_thresh`` -> descartar (casi constante).
    - ``atributo_pozo`` no numérico (p. ej. ``sigla``) -> descartar (id legible).
    - numérica -> ``histograma``; categórica -> ``frecuencia``.

    Devuelve la tabla de stats con dos columnas extra (``decision``, ``motivo``),
    ordenada por tipo de análisis.
    """
    rep = _column_stats(df)
    decisions, motivos = [], []
    for col, r in rep.iterrows():
        rol = r["rol"]
        is_numeric = col in NUMERIC_COLS
        if rol == "identificador":
            d, m = "descartar", "identificador (clave)"
        elif rol == "metadata":
            d, m = "descartar", "metadata de carga/auditoría"
        elif rol == "temporal" or col in _TEMPORAL_INDEX:
            d, m = "descartar", "eje temporal (no es feature)"
        elif r["pct_null"] >= null_thresh:
            d, m = "descartar", f"casi todo null ({r['pct_null']:.0f}%)"
        elif r["n_unique"] <= 1:
            d, m = "descartar", "constante (1 valor)"
        elif r["pct_dominante"] >= dominant_thresh:
            d, m = "descartar", f"casi constante ({r['pct_dominante']:.0f}% un valor)"
        elif rol == "atributo_pozo" and not is_numeric:
            d, m = "descartar", "id legible del pozo (alta cardinalidad)"
        elif is_numeric:
            d, m = "histograma", "medida/atributo numérico"
        else:
            d, m = "frecuencia", "categórica"
        decisions.append(d)
        motivos.append(m)
    rep["decision"] = decisions
    rep["motivo"] = motivos
    orden = {"histograma": 0, "frecuencia": 1, "descartar": 2}
    return rep.sort_values(
        by=["decision", "rol", "columna"], key=lambda s: s.map(orden) if s.name == "decision" else s
    )


def numeric_eda_cols(df: pd.DataFrame) -> list[str]:
    """Numéricas que sí se analizan (decision == 'histograma'), en orden del plan."""
    plan = column_eda_plan(df)
    return list(plan.index[plan["decision"] == "histograma"])


def categorical_eda_cols(df: pd.DataFrame) -> list[str]:
    """Categóricas que sí se analizan (decision == 'frecuencia'), en orden del plan."""
    plan = column_eda_plan(df)
    return list(plan.index[plan["decision"] == "frecuencia"])


def plot_numeric_histograms(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    clip: tuple[float, float] = (1, 99),
    bins: int = 40,
    ncols: int = 3,
):
    """Grilla de histogramas de las variables numéricas analizables.

    Las medidas de producción son muy asimétricas (cola larga + masa en 0), así
    que recortamos a ``[p{clip[0]}, p{clip[1]}]`` para ver el grueso de la
    distribución. En cada panel se anota el % de ceros, que es información
    relevante (pozos sin producción ese mes) que el recorte podría tapar.
    """
    if cols is None:
        cols = numeric_eda_cols(df)
    n = len(cols)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 3.2 * nrows))
    axes = np.atleast_1d(axes).ravel()
    for ax, col in zip(axes, cols):
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        lo, hi = np.percentile(s, clip)
        clipped = s[(s >= lo) & (s <= hi)]
        ax.hist(clipped, bins=bins, color="#4c78a8", edgecolor="white", linewidth=0.3)
        unidad = RAW_COLUMNS.get(col, ("", ""))[1]
        ax.set_title(f"{col}  [{unidad}]", fontsize=10)
        pct_zero = (s == 0).mean() * 100
        ax.text(
            0.97, 0.93, f"ceros: {pct_zero:.0f}%\nrango p{int(clip[0])}–p{int(clip[1])}",
            transform=ax.transAxes, ha="right", va="top", fontsize=7.5, color="#555",
        )
        ax.set_ylabel("frecuencia", fontsize=8)
        ax.tick_params(labelsize=7.5)
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle(
        f"Histogramas — variables numéricas (train, recorte p{int(clip[0])}–p{int(clip[1])})",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


def plot_categorical_frequencies(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    top: int = 15,
    ncols: int = 2,
):
    """Grilla de frecuencias (% de filas) de las categóricas analizables.

    Para las de alta cardinalidad (empresa, áreas) se muestran las ``top``
    categorías más frecuentes; el título indica cuántas quedaron fuera.
    """
    if cols is None:
        cols = categorical_eda_cols(df)
    n = len(cols)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(6.5 * ncols, 3.0 * nrows))
    axes = np.atleast_1d(axes).ravel()
    total = len(df)
    for ax, col in zip(axes, cols):
        vc = df[col].value_counts(dropna=False).head(top)
        pct = vc / total * 100
        y = range(len(vc))
        ax.barh(list(y), pct.values, color="#72b7b2", edgecolor="white", linewidth=0.3)
        ax.set_yticks(list(y))
        ax.set_yticklabels([str(i)[:24] for i in vc.index], fontsize=8)
        ax.invert_yaxis()
        nun = df[col].nunique(dropna=True)
        extra = f"  (top {top} de {nun})" if nun > top else ""
        ax.set_title(f"{col}{extra}", fontsize=10)
        ax.set_xlabel("% de filas", fontsize=8)
        ax.tick_params(labelsize=7.5)
    for ax in axes[n:]:
        ax.axis("off")
    fig.suptitle("Frecuencias — variables categóricas (train)", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return fig


# --- ¿Qué columnas tiene sentido pasarle al modelo? -----------------------
# OJO: es una decisión DISTINTA de la del EDA. En el EDA descartamos lo que no
# se grafica (ids, eje temporal); acá decidimos qué entra como *input* del
# modelo. Algunas columnas que no graficamos sí juegan un rol en el modelado:
#   - los identificadores no entran crudos, pero son la clave para construir
#     features (lags y agregados por pozo/empresa/área) -> "feature_engineering".
#   - 'anio'/'mes' no son distribuciones interesantes, pero sí aportan señal
#     temporal (estacionalidad por mes; tendencia por año) -> "input".
# Es una decisión de diseño, no algo que se derive solo de los datos, así que
# va curada a mano (con los stats al lado como apoyo).
#
# Categorías de `uso_modelo`:
#   target              -> lo que se predice (prod_pet).
#   input               -> candidata a entrar como feature al modelo.
#   feature_engineering -> no entra cruda; sirve para construir features/panel.
#   descartar           -> no aporta (constante, casi vacía o metadata de carga).
MODEL_INPUT_ROLE: dict[str, tuple[str, str]] = {
    # --- Target ---
    "prod_pet": ("target", "lo que se predice (t+1)."),
    # --- Inputs numéricos ---
    "prod_gas":   ("input", "co-producción; útil como lag/contexto del pozo."),
    "prod_agua":  ("input", "co-producción (corte de agua); lag/contexto."),
    "tef":        ("input", "tiempo efectivo del mes; explica volumen producido."),
    "profundidad":("input", "atributo estático del pozo (proxy de geología)."),
    "coordenadax":("input", "ubicación; puede capturar área/geología."),
    "coordenaday":("input", "ubicación; puede capturar área/geología."),
    # --- Tiempo: no se grafican como distribución, pero sí aportan al modelo ---
    "anio":       ("input", "tendencia temporal (ojo extrapolación fuera de train)."),
    "mes":        ("input", "estacionalidad; conviene codificarlo cíclico (sin/cos)."),
    # --- Inputs categóricos ---
    "tipoextraccion":  ("input", "método de extracción; encodear."),
    "tipoestado":      ("input", "estado del pozo en el mes; encodear."),
    "tipopozo":        ("input", "tipo de pozo; encodear."),
    "formprod":        ("input", "formación productiva; encodear."),
    "formacion":       ("input", "formación geológica; encodear."),
    "clasificacion":   ("input", "explotación/exploración; encodear."),
    "subclasificacion":("input", "subclasificación; encodear."),
    "sub_tipo_recurso":("input", "SHALE/TIGHT; encodear."),
    "proyecto":        ("input", "proyecto asociado; encodear."),
    "empresa":         ("input", "operadora; alta cardinalidad -> target/freq encoding."),
    "areapermisoconcesion": ("input", "área; alta cardinalidad -> encoding cuidadoso."),
    "areayacimiento":  ("input", "yacimiento; alta cardinalidad -> encoding cuidadoso."),
    "cuenca":          ("input", "casi constante (NEUQUINA); aporta poco."),
    "provincia":       ("input", "casi constante (Neuquén); aporta poco."),
    # --- Claves: no entran crudas, pero arman el panel y las features ---
    "idpozo":    ("feature_engineering", "clave del panel: lags y agregados por pozo."),
    "idempresa": ("feature_engineering", "clave de agrupación por operadora."),
    "idareapermisoconcesion": ("feature_engineering", "clave de agrupación por área."),
    "idareayacimiento":       ("feature_engineering", "clave de agrupación por yacimiento."),
    # --- Descartar ---
    "iny_agua": ("descartar", "casi constante (≈100% en 0)."),
    "iny_gas":  ("descartar", "casi constante (≈100% en 0)."),
    "iny_co2":  ("descartar", "constante (todo 0)."),
    "iny_otro": ("descartar", "constante (todo 0)."),
    "tipo_de_recurso": ("descartar", "constante (NO CONVENCIONAL)."),
    "vida_util":   ("descartar", "98% null."),
    "sigla":       ("descartar", "id legible redundante con idpozo."),
    "observaciones":("descartar", "94% null; texto libre."),
    "fechaingreso":("descartar", "metadata de carga (fuga: posterior al periodo)."),
    "fecha_data":  ("descartar", "metadata de versionado del dato."),
    "rectificado": ("descartar", "metadata de auditoría; casi constante."),
    "habilitado":  ("descartar", "metadata de auditoría; constante."),
    "idusuario":   ("descartar", "metadata de carga (quién cargó el registro)."),
}


def model_input_plan(df: pd.DataFrame) -> pd.DataFrame:
    """Decisión de qué columna entra al modelo y cómo (``uso_modelo``).

    Distinta de :func:`column_eda_plan`: acá no se decide qué graficar sino qué
    usar como input. Devuelve los stats base + ``uso_modelo`` y ``motivo_modelo``
    (curados en ``MODEL_INPUT_ROLE``), ordenados por rol.
    """
    rep = _column_stats(df)
    rep["uso_modelo"] = [MODEL_INPUT_ROLE.get(c, ("—", ""))[0] for c in rep.index]
    rep["motivo_modelo"] = [MODEL_INPUT_ROLE.get(c, ("", "—"))[1] for c in rep.index]
    orden = {"target": 0, "input": 1, "feature_engineering": 2, "descartar": 3}
    return rep.sort_values(
        by=["uso_modelo", "rol", "columna"],
        key=lambda s: s.map(orden) if s.name == "uso_modelo" else s,
    )


def numeric_model_features(df: pd.DataFrame) -> list[str]:
    """Features de entrada **numéricas** (uso_modelo == 'input' y dtype numérico).

    Incluye las medidas/atributos continuos y el eje temporal (``anio``/``mes``).
    No incluye el target.
    """
    return [
        c for c in df.columns
        if MODEL_INPUT_ROLE.get(c, ("",))[0] == "input"
        and pd.api.types.is_numeric_dtype(df[c])
    ]


def categorical_model_features(df: pd.DataFrame) -> list[str]:
    """Features de entrada **categóricas** (uso_modelo == 'input' y no numéricas)."""
    return [
        c for c in df.columns
        if MODEL_INPUT_ROLE.get(c, ("",))[0] == "input"
        and not pd.api.types.is_numeric_dtype(df[c])
    ]


# --- Correlación de las features candidatas -------------------------------

def numeric_correlation(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    include_target: bool = True,
    method: str = "pearson",
) -> pd.DataFrame:
    """Matriz de correlación de las features numéricas (con el target adelante).

    ``method`` puede ser ``"pearson"`` (lineal) o ``"spearman"`` (de rango, más
    robusto a la fuerte asimetría de las medidas de producción).
    """
    if cols is None:
        cols = numeric_model_features(df)
    if include_target and TARGET not in cols:
        cols = [TARGET] + cols
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    return num.corr(method=method)


def plot_numeric_correlation(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    include_target: bool = True,
    method: str = "pearson",
):
    """Heatmap anotado de la matriz de correlación de las features numéricas."""
    corr = numeric_correlation(df, cols, include_target=include_target, method=method)
    k = len(corr)
    fig, ax = plt.subplots(figsize=(0.85 * k + 2.5, 0.75 * k + 2))
    im = ax.imshow(corr.values, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(k))
    ax.set_xticklabels(corr.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(k))
    ax.set_yticklabels(corr.index, fontsize=8)
    for i in range(k):
        for j in range(k):
            v = corr.iloc[i, j]
            ax.text(
                j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                color="white" if abs(v) > 0.55 else "#222",
            )
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title(f"Correlación {method} — features numéricas (train)", fontsize=11)
    fig.tight_layout()
    return fig


def categorical_target_correlation(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    target: str = TARGET,
) -> pd.DataFrame:
    """Correlación de cada categoría one-hot con el target.

    Para cada feature categórica de entrada se hace one-hot (``pd.get_dummies``)
    y se calcula la correlación de cada dummy 0/1 con el target continuo
    (correlación punto-biserial). Devuelve una tabla larga
    ``feature / categoria / pct_filas / corr_target`` ordenada por |corr|.
    """
    if cols is None:
        cols = categorical_model_features(df)
    y = pd.to_numeric(df[target], errors="coerce")
    recs = []
    for col in cols:
        dummies = pd.get_dummies(df[col], prefix=col, prefix_sep="=").astype(float)
        corr = dummies.corrwith(y)
        freq = dummies.mean() * 100
        for cat in corr.index:
            recs.append(
                {
                    "feature": col,
                    "categoria": cat.split("=", 1)[1],
                    "pct_filas": round(freq[cat], 1),
                    "corr_target": round(corr[cat], 3),
                }
            )
    out = pd.DataFrame(recs).dropna(subset=["corr_target"])
    order = out["corr_target"].abs().sort_values(ascending=False).index
    return out.loc[order].reset_index(drop=True)


def plot_categorical_target_correlation(
    df: pd.DataFrame,
    cols: list[str] | None = None,
    *,
    target: str = TARGET,
    top: int = 25,
):
    """Barras de las ``top`` categorías one-hot con mayor |correlación| con el target."""
    tbl = categorical_target_correlation(df, cols, target=target).head(top)
    labels = tbl["feature"] + "=" + tbl["categoria"].astype(str)
    colors = ["#d65f5f" if v < 0 else "#4c78a8" for v in tbl["corr_target"]]
    fig, ax = plt.subplots(figsize=(7.5, 0.33 * len(tbl) + 1.2))
    ax.barh(range(len(tbl)), tbl["corr_target"], color=colors, edgecolor="white", linewidth=0.3)
    ax.set_yticks(range(len(tbl)))
    ax.set_yticklabels([str(lbl)[:42] for lbl in labels], fontsize=7.5)
    ax.invert_yaxis()
    ax.axvline(0, color="#999", linewidth=0.8)
    ax.set_xlabel(f"correlación con {target}", fontsize=9)
    ax.set_title(f"Top {top} categorías (one-hot) por |correlación| con {target} — train", fontsize=10)
    fig.tight_layout()
    return fig
