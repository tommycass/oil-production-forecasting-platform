# Runbook — Usuario de BI

**Procedimiento:** Explorar y analizar datos de producción de pozos no convencionales
en Metabase, verificar la frescura del pipeline y construir preguntas ad-hoc sin
escribir SQL.

Rol de perfil de negocio. Complementa el [modelo de datos](../data-model.md),
el [handoff del DW](../handoff-dw-bi-gobierno.md) y el
[ADR-020](../adr/0020-plataforma-bi.md) (elección de Metabase).

---

## 1. Propósito y disparador

Permitir que un analista de negocio o responsable de operaciones consulte los datos de
producción de petróleo y gas sin necesidad de acceso directo al Data Warehouse ni
conocimiento de SQL. Se ejecuta cuando:

- **Revisión periódica:** cierre de mes, preparación de reportes para gerencia o
  reguladores.
- **Consulta puntual:** validar la producción de un yacimiento o pozo específico ante
  una pregunta del negocio.
- **Verificación de frescura:** confirmar que el pipeline corrió correctamente antes de
  usar los datos para una decisión.
- **Análisis exploratorio:** identificar tendencias, outliers o pozos de bajo rendimiento
  sin necesidad de una nueva ETL.

---

## 2. Rol / dueño y prerrequisitos

- **Dueño:** analista de negocio, responsable de operaciones o data analyst con acceso
  de lectura a Metabase.
- **No se requiere:** acceso al Data Warehouse, conocimiento de SQL ni acceso SSH a
  las EC2.
- **Prerrequisitos:**
  - Credenciales de Metabase (rol Viewer o superior). Solicitarlas al administrador
    del sistema si no se tienen.
  - Acceso de red al host de producción (VPN si aplica, o red pública en el entorno
    actual).
  - Saber que los datos tienen una latencia de hasta un mes respecto de la fuente
    (ver §6).

---

## 3. Pasos

### 3.1 Acceder al sistema

1. Abrir en el navegador: `http://<ip>:3001`
2. Ingresar con las credenciales proporcionadas por el administrador.
3. En el menú izquierdo, ir a **Nuestros análisis** para ver los dashboards disponibles.

### 3.2 Revisar el dashboard principal

1. Hacer clic en **Producción No Convencional**.
2. Verificar la tarjeta **Última Corrida Pipeline**: indica cuándo corrió el pipeline
   por última vez. Si la fecha es mayor a 35 días, el pipeline puede tener un
   problema — ver §5.
3. Verificar **Checks de Calidad OK**: debe mostrar `31`. Si hay checks fallidos,
   los datos de Gold pueden estar incompletos — consultar al Analytics Engineer.
4. Verificar **Último Período en Gold**: indica el mes más reciente con datos
   cargados. Recordar que la fuente publica con retraso de ~30 días.

### 3.3 Explorar producción por yacimiento

1. En el gráfico **Producción mensual por yacimiento**, pasar el cursor sobre
   cualquier barra para ver el desglose por yacimiento y mes.
2. Hacer clic en un yacimiento de la leyenda para filtrarlo o silenciarlo.
3. Para ver el detalle en tabla: hacer clic en el ícono `···` del gráfico →
   **Ver datos**.

### 3.4 Identificar los pozos más productivos

1. El gráfico **Top 8 pozos por producción** muestra el ranking histórico total.
2. Para filtrar por período o yacimiento, usar el botón **Filtrar** del dashboard
   (ícono de embudo arriba a la derecha del dashboard en modo edición).

### 3.5 Construir una pregunta ad-hoc (sin SQL)

Metabase permite explorar datos sin escribir código. Para usuarios sin conocimiento
del modelo estrella se recomienda el esquema **`semantic`**; para análisis avanzados
con joins cruzados, usar el esquema **`gold`**.

#### Opción A — Esquema `semantic` (recomendada para usuarios no técnicos)

Las vistas semánticas ya aplican todos los joins y exponen nombres en lenguaje de negocio:

1. Ir a **+ Nuevo → Pregunta**.
2. Seleccionar la base **Oil DW Producción** → esquema **semantic**.
3. Elegir la vista según el análisis deseado:

| Vista | Uso recomendado |
|---|---|
| `sem_produccion_mensual_por_yacimiento` | Producción por yacimiento y período |
| `sem_top_pozos` | Ranking histórico de pozos |
| `sem_kpi_pipeline` | Frescura y estado operativo del pipeline |
| `sem_produccion_anual_por_operadora` | Comparativa anual por empresa |

4. Usar **Resumir** para agregar métricas (las columnas `prod_pet_total_m3`, etc., ya están pre-sumadas por período; si se aplica un SUM sobre ellas con más agrupaciones, el resultado es correcto).
5. Usar **Filtrar** para acotar por yacimiento, período u otra dimensión.
6. Cambiar la visualización con el botón **Visualización** (línea, barra, tabla, etc.).
7. Guardar con **Guardar** → asignar un nombre descriptivo → **Guardar en Nuestros análisis**.

#### Opción B — Esquema `gold` (para usuarios con conocimiento del modelo estrella)

1. Ir a **+ Nuevo → Pregunta**.
2. Seleccionar la base **Oil DW Producción** → esquema **gold**.
3. Elegir la tabla de inicio (por ejemplo, `Fact Produccion Mensual`).
4. Usar **Resumir** para agregar métricas (suma de `prod_pet`, promedio de `tef`).
5. Usar **Filtrar** para acotar por yacimiento, período o cualquier dimensión.
6. Cambiar la visualización y guardar como en la Opción A.

> **Precaución al agregar métricas en `gold`:**
> - `prod_pet`, `prod_gas`, `prod_agua`, `iny_*`: sumar con SUM.
> - `tef` (tiempo efectivo de producción): promediar con AVG, nunca sumar.
> - Para el eje temporal, usar `dim_fecha.periodo` (formato `AAAA-MM`).
> - Los joins van por columnas `sk_*` (surrogate keys enteras).

### 3.6 Compartir un análisis

1. Desde cualquier pregunta o dashboard, hacer clic en el ícono de compartir (↗).
2. Activar **Compartir públicamente** para generar un enlace sin autenticación
   (solo lectura).
3. Para enviar por correo: **Suscripciones** → configurar frecuencia y destinatarios.

---

## 4. Validación

El análisis está correcto cuando:

- La tarjeta **Última Corrida Pipeline** tiene fecha no mayor a 35 días.
- **Checks de Calidad OK** = 31 (sin checks fallidos ni en advertencia crítica).
- Los totales de `prod_pet` para el período más reciente son coherentes con los
  reportes anteriores (variaciones superiores al 30% intermensual merecen revisión).
- El gráfico de tendencia muestra la curva de crecimiento esperada para producción
  no convencional (tendencia ascendente sostenida desde 2015, con estacionalidad
  menor).

---

## 5. Si algo falla

| Síntoma | Causa probable | Acción |
|---|---|---|
| No se puede acceder a `18.117.126.59:3001` | Metabase caído o red sin acceso | Avisar al administrador de gobierno/infra; mientras tanto usar los datos de la sesión anterior |
| Fecha de última corrida > 35 días | Pipeline no corrió o falló | Escalar al Analytics Engineer; no usar los datos para decisiones críticas |
| Checks de Calidad OK < 31 | Un check de datos falló en la última corrida | Escalar al Analytics Engineer; revisar `dq.dq_results` si se tiene acceso al DW |
| Datos del mes esperado ausentes | Fuente aún no publicó o pipeline aún no corrió | La fuente publica con ~30 días de retraso; esperar al siguiente ciclo |
| Error "No autorizado" al guardar una pregunta | Credenciales de Viewer no permiten escritura | Solicitar al administrador un rol con permisos de edición |

**A quién escalar:**
- Problemas de acceso o credenciales → administrador de infraestructura/gobierno.
- Datos faltantes, checks fallidos o pipeline detenido → Analytics Engineer.
- Preguntas sobre definición de métricas o lógica de negocio → Data PM / owner.

---

## 6. Consideraciones no funcionales

### Frescura de los datos

Los datos en Gold tienen una **latencia máxima de aproximadamente un mes** respecto
de la fuente (datos.gob.ar, Ministerio de Energía). Esto se debe a dos factores
acumulados: la fuente publica con un retraso propio de ~30 días desde el cierre del
período, y el pipeline corre mensualmente (el 5 de cada mes). En consecuencia,
Metabase no es adecuado para análisis de producción en tiempo real ni para alertas
operativas; su valor está en el análisis de tendencias, comparaciones históricas y
reporting estratégico.

**Decisión funcional:** el contrato primario de BI con el DW es el esquema `semantic.*`
(vistas semánticas pre-unificadas sobre Gold, ver [ADR-027](../adr/0027-semantic-layer.md))
para usuarios no técnicos, y `gold.*` para analistas con conocimiento del modelo
estrella. Los esquemas `silver.*` (datos limpios pero sin el modelo dimensional) y
`bronze.*` (crudo) no se exponen en los dashboards principales. Esta decisión la ownea
el Analytics Engineer en coordinación con el usuario de BI: mantener el contrato estable
en `semantic`/`gold` significa que los dashboards no se rompen cuando B cambia la lógica
de limpieza en Silver; el analista de negocio nunca necesita saber qué pasó en capas
anteriores. Si una métrica de negocio cambia de definición, la conversación ocurre en las
vistas semánticas o en el modelo dimensional (Gold), no en los dashboards.

**Decisión no funcional:** la latencia de un mes es aceptable para el caso de uso
actual (reporting estratégico de producción de pozos no convencionales en Argentina),
donde las decisiones operativas tienen horizontes de semanas o meses, no de horas.
Reducir la latencia requeriría cambiar la cadencia del pipeline y la fuente de datos;
el costo operativo de esa mejora no se justifica para el volumen y la frecuencia de
consultas actuales. Esta restricción debe comunicarse explícitamente a cualquier
usuario que quiera usar los dashboards para decisiones que requieran datos de la
semana en curso.

### Seguridad y privacidad

Los datos expuestos en Metabase son datos de producción de pozos de acceso público
(fuente: Ministerio de Energía, datos.gob.ar). No contienen información personal ni
datos confidenciales de operadoras más allá de lo que ya es público. Sin embargo,
el acceso a Metabase está protegido por autenticación para evitar exposición de
análisis internos o de configuraciones del DW.

### Disponibilidad

Metabase depende de que el contenedor `oil-metabase` esté corriendo en la EC2 de
producción. Si el host se reinicia, el contenedor no arranca automáticamente a menos
que la política de reinicio esté configurada (`restart: unless-stopped`, configurado).
En caso de caída, el tiempo de recuperación es el tiempo de reinicio del contenedor
(~90 segundos por las migraciones internas de Metabase).
