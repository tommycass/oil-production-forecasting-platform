"""Tests de la lectura del dataset de entrenamiento desde el feature store
(ADR-035, Revisión jul-2026).

``build_dataset_from_store`` es el seam por el que training/eval/baselines leen la MISMA
tabla que sirve la inferencia (cero training-serving skew). Su lógica **propia** —lo único
que no delega en el store— es lo que se testea acá, aislando el I/O de la DB (se mockea
``read_sql``, no se hardcodea el resultado: se le da una tabla cruda representativa y se
verifica la TRANSFORMACIÓN):

1. **Filtrar ``y_next`` no nulo:** el store conserva la última fila de cada pozo con
   ``y_next`` NULL (para que la API infiera el mes siguiente); esas filas NO son ejemplos
   de entrenamiento y se descartan.
2. **Derivar el ``split`` temporal** por ``periodo`` (ADR-028/031) — con foco en los
   **bordes** ``TRAIN_END`` / ``VAL_END``, que es donde un off-by-one rompería el split.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd

from ml import dataset
from ml.config import split_bounds


def _tabla_store(filas) -> pd.DataFrame:
    """Arma lo que devolvería ``select * from features.feat_...``: claves + una feature del
    target + ``y_next``. ``filas`` = lista de ``(idpozo, periodo, y_next)``."""
    reg = [(p, per, pd.Timestamp(per) + pd.DateOffset(months=1), 100.0, y) for p, per, y in filas]
    return pd.DataFrame(reg, columns=["idpozo", "periodo", "periodo_objetivo", "prod_pet", "y_next"])


def _leer(df):
    """Corre build_dataset_from_store con la tabla ``df`` mockeando solo el I/O de DB."""
    with patch.object(dataset, "_store_engine", return_value=object()), \
         patch.object(dataset.pd, "read_sql", return_value=df):
        return dataset.build_dataset_from_store("prod_pet")


def test_descarta_filas_de_inferencia_y_next_nulo():
    """Las filas con ``y_next`` NULL (última de cada pozo en el store) no son ejemplos de
    entrenamiento: se descartan. El resto se conserva."""
    ds = _leer(_tabla_store([
        (1, "2022-01-01", 10.0),
        (1, "2022-02-01", np.nan),   # inferencia -> fuera
        (2, "2022-01-01", 30.0),
        (2, "2022-03-01", np.nan),   # inferencia -> fuera
    ]))
    assert len(ds) == 2
    assert ds["y_next"].notna().all()
    assert set(ds["idpozo"]) == {1, 2}


def test_split_bounds_reproduce_el_split_documentado():
    """Calibración (ADR-028): con las ventanas por defecto (18/16), el snapshot actual
    (max 2026-05) da exactamente el split documentado — 2023-07 / 2024-11."""
    train_end, val_end = split_bounds(pd.Timestamp("2026-05-01"))
    assert train_end == pd.Timestamp("2023-07-01")
    assert val_end == pd.Timestamp("2024-11-01")


def test_split_se_deriva_de_la_ultima_fecha_observada():
    """El split se deriva de la última fecha observada, tomada del store completo (incluida
    la fila de inferencia y_next NULL, que fija el anchor aunque luego se descarte). Se
    prueban los bordes exactos, donde un off-by-one rompería o desalinearía el split."""
    m = pd.Timestamp("2026-04-01")                       # última fecha observada (inferencia)
    train_end, val_end = split_bounds(m)
    post_train = (train_end + pd.DateOffset(months=1)).date().isoformat()
    post_val = (val_end + pd.DateOffset(months=1)).date().isoformat()

    ds = _leer(_tabla_store([
        (1, "2010-01-01", 1.0),                          # bien dentro de train
        (1, train_end.date().isoformat(), 2.0),          # BORDE: == train_end -> train
        (1, post_train, 3.0),                            # BORDE: > train_end  -> val
        (1, val_end.date().isoformat(), 4.0),            # BORDE: == val_end   -> val
        (1, post_val, 5.0),                              # BORDE: > val_end    -> test
        (1, m.date().isoformat(), np.nan),               # inferencia: fija el anchor, se descarta
    ]))
    # la fila de inferencia (anchor) no es ejemplo de entrenamiento: se descarta
    assert m not in set(ds["periodo"])
    split_por_periodo = dict(zip(ds["periodo"].dt.date.astype(str), ds["split"]))
    assert split_por_periodo["2010-01-01"] == "train"
    assert split_por_periodo[train_end.date().isoformat()] == "train"
    assert split_por_periodo[post_train] == "val"
    assert split_por_periodo[val_end.date().isoformat()] == "val"
    assert split_por_periodo[post_val] == "test"


def test_periodo_datetime_y_columnas_clave():
    """La lectura tipa ``periodo``/``periodo_objetivo`` como datetime (para el split y el
    contrato de claves) y preserva claves + features + y_next + split."""
    ds = _leer(_tabla_store([(1, "2022-01-01", 10.0)]))
    for col in ("idpozo", "periodo", "periodo_objetivo", "prod_pet", "y_next", "split"):
        assert col in ds.columns
    assert pd.api.types.is_datetime64_any_dtype(ds["periodo"])
    assert pd.api.types.is_datetime64_any_dtype(ds["periodo_objetivo"])


def test_usa_la_tabla_del_target_pedido():
    """El target elige la tabla del store (petróleo vs gas, ADR-039): se verifica que la
    query apunte a la tabla correcta."""
    capturado = {}

    def _fake_read_sql(query, _engine):
        capturado["query"] = query
        return _tabla_store([(1, "2022-01-01", 10.0)])

    with patch.object(dataset, "_store_engine", return_value=object()), \
         patch.object(dataset.pd, "read_sql", side_effect=_fake_read_sql):
        dataset.build_dataset_from_store("prod_gas")

    assert "feat_produccion_pozo_mensual_gas" in capturado["query"]
