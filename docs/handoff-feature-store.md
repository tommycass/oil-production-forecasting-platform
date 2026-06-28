# Handoff — consumir el feature store

> **De:** Rol 2 (Feature Store + Orquestación) · **Para:** Rol 1 (entrenamiento) y Rol 3 (inferencia).
> Acompaña al contrato [docs/feature-store.md](feature-store.md) y a [ADR-031](adr/0031-feature-store.md). El diseño del problema está en [ADR-028](adr/0028-diseno-problema-modelado.md).

El feature store ya está implementado: la tabla **`features.feat_produccion_pozo_mensual`** (dbt, esquema `features` del DW) reproduce **exacto** las features de `ml/dataset.py` (verificado 1-a-1 contra pandas). El objetivo es que entrenamiento e inferencia lean **las mismas** features para evitar el *training-serving skew* (RNF Fase 3): la feature se calcula **una sola vez** en el store.

## Rol 1 — entrenamiento (`ml/dataset.py`)

`ml/dataset.py` deja de leer el CSV y de calcular features: ahora **lee la tabla**. Las columnas tienen los **mismos nombres** (`lag1/lag2/lag3/roll3/antiguedad/tef_lag1/y_next`), así que el modelo no cambia; solo el origen de los datos.

```python
# ml/config.py — DATA_CSV queda deprecado; sumar:
import os
def pg_url():
    return (f"postgresql+psycopg2://{os.getenv('POSTGRES_USER','oil')}:"
            f"{os.getenv('POSTGRES_PASSWORD','oil')}@{os.getenv('POSTGRES_HOST','localhost')}:"
            f"{os.getenv('POSTGRES_PORT','5432')}/{os.getenv('POSTGRES_DB','oil_dw')}")
FEATURE_TABLE = "features.feat_produccion_pozo_mensual"

# ml/dataset.py
from sqlalchemy import create_engine

def load_features():
    df = pd.read_sql(f"select * from {FEATURE_TABLE}", create_engine(pg_url()),
                     parse_dates=["periodo"])
    return df.sort_values(["idpozo", "periodo"]).reset_index(drop=True)

def add_baselines(df):                 # se mantiene
    df["b_persist"] = df["prod_pet"]
    df["b_ma3"]     = df["roll3"]       # ya viene del store
    df["b_seas"]    = df.groupby("idpozo")["prod_pet"].shift(11)
    return df

# add_target() y add_features() YA NO HACEN FALTA:
#   y_next, lag1/2/3, roll3, antiguedad, tef_lag1 vienen del store.
# add_split() queda IGUAL (usa periodo + TRAIN_END/VAL_END de config).

def build_modeling_frame():
    return add_split(add_baselines(load_features()))
```

**Notas:**
- **Universo y anti-leakage ya aplicados** en el store (pozos con `prod_pet>0`; features as-of `t`; `y_next` = `t+1`). No re-filtrar ni recalcular.
- **Entrenamiento:** usar filas con `y_next IS NOT NULL` (las de `y_next` nulo son la última de cada pozo → para inferencia). `baseline.py` ya hace `y_next.notna()`.
- **`periodo` es `date`** → el split por `TRAIN_END`/`VAL_END` funciona sin cambios.
- **Conexión por env vars `POSTGRES_*`** (en local, el container `local-db` oil/oil/oil_dw; en AWS, el RDS). Mismo patrón que `transform/scripts/load_bronze.py`.
- El store sale de **Gold** (deduplicado, sin los negativos que van a cuarentena), así que los conteos pueden diferir un poco del CSV viejo; el split por fecha (ADR-028) es reproducible igual.
- **¿Feature nueva?** Pedímela y la agrego al store (con bump de `feature_set_version`); no la recalcules en pandas, para no reintroducir skew.

## Rol 3 — inferencia (`POST /api/v1/predict`)

Dado `(idpozo, mes_objetivo)`: leer la fila del **mes base** `t = mes_objetivo - 1` de `features.feat_produccion_pozo_mensual`, tomar las columnas de features y pasarlas al modelo marcado `Production` en MLflow. **No recalcular features.** Si no hay fila para ese pozo/mes (pozo sin historia), devolver el error de contrato que definas.

## Columnas disponibles

Claves: `idpozo`, `anio`, `mes`, `periodo` (date), `trimestre`. Estáticas: `profundidad`, `formacion`, `tipopozo`, `clasificacion`, `cuenca`, `provincia`, `operadora`. Medidas de `t`: `prod_pet`, `prod_gas`, `tef`. Features: `lag1`, `lag2`, `lag3`, `roll3`, `antiguedad`, `tef_lag1`. Target: `y_next`. Metadata: `feature_set_version`, `computed_at`. (Detalle de cada una en el contrato.)
