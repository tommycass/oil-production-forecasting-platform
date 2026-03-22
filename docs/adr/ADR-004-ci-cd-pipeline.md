# ADR-004 — Pipeline de CI/CD y estrategia de testing

**Fecha:** Marzo 2026  
**Estado:** Aceptado  
**Autor:** Castro Tomás  

---

## Contexto

El equipo trabaja de forma distribuida con un modelo de ramas por funcionalidad y Pull
Requests hacia `develop`. Sin automatización, la calidad del código depende de revisiones
manuales que son propensas a error humano y no escalan. La consigna requiere
explícitamente un pipeline de CI/CD que ejecute tests y construya la imagen Docker en
cada PR.

---

## Problema

¿Cómo garantizar que cada cambio mergeado a `develop` no rompe el sistema, que la
imagen Docker es siempre construible, y que las imágenes publicadas son seguras y
verificadas? ¿Dónde y cómo se organizan los tests automáticos?

---

## Alternativas consideradas

### Alternativa A — Sin CI, tests solo locales
- Cada desarrollador corre los tests en su máquina antes de hacer push.
- **Ventaja:** cero configuración de infraestructura.
- **Desventaja:** no hay garantía de que los tests se corran. Un PR puede mergearse con
  código roto. No hay trazabilidad del resultado de los tests por commit.

### Alternativa B — GitHub Actions con job único
- Un solo job que instala dependencias, corre tests y construye la imagen.
- **Ventaja:** configuración más simple.
- **Desventaja:** si el build de Docker falla, no se puede distinguir si el problema es
  el código (tests) o la imagen. Además, el build corre aunque los tests fallen,
  desperdiciando recursos.

### Alternativa C — GitHub Actions con jobs secuenciales (`test` → `build`)
- Job `test`: análisis estático + ejecución de tests.
- Job `build`: construcción de imagen Docker, escaneo de vulnerabilidades, verificación
  de salud del contenedor y publicación en registry. Condicionado a que `test` pase.
- **Ventaja:** separación clara de responsabilidades. El build no corre si el código
  está roto. Los errores son fáciles de diagnosticar por job.
- **Desventaja:** configuración ligeramente más compleja.

---

## Decisión

Se implementa la **Alternativa C**: dos jobs secuenciales en GitHub Actions.

### Estructura del pipeline

```
CI
├── test
│   ├── Instalación de dependencias (requirements.txt + requirements-dev.txt)
│   ├── Análisis estático con ruff (api/app/)
│   └── Ejecución de pytest (api/tests/) con API_KEY inyectada como env var
└── build  (solo si test pasa)
    ├── docker build -f infra/Dockerfile api/
    ├── Escaneo de vulnerabilidades con Trivy (CRITICAL, HIGH)
    ├── Verificación de salud: levanta el contenedor y llama a GET /health
    ├── Login a GHCR  (solo en push, no en PRs)
    └── Push a ghcr.io/<owner>/oil-forecast-api:latest y :<sha>  (solo en push)
```

**Trigger:** el pipeline se ejecuta en cada `push` y `pull_request` hacia `develop`
y `main`.

### Decisiones de implementación

**Herramienta:** GitHub Actions — elegida por estar integrada nativamente con el
repositorio, sin necesidad de servicios externos. No requiere configuración adicional
de tokens ni webhooks.

**Análisis estático:** se usa `ruff` en lugar de `flake8` + `black` por ser una
herramienta única que combina linting y formato, con velocidad significativamente mayor.
El análisis corre solo sobre `api/app/` (código de producción), no sobre los tests.

**Variables de entorno en CI:** la `API_KEY` se inyecta como variable de entorno en el
job de tests (`env: API_KEY: abcdef12345`). Esta es la API key de desarrollo definida
en la consigna. Para producción, se usaría un GitHub Secret.

**Separación de dependencias:** las dependencias de testing se mantienen en
`requirements-dev.txt`, separadas de `requirements.txt`. Esto permite que la imagen
Docker de producción no incluya herramientas de testing.

**Escaneo de vulnerabilidades con Trivy:** se usa `aquasecurity/trivy-action` sobre la
imagen construida. El `exit-code` está configurado en `0` (modo informativo): el pipeline
reporta vulnerabilidades CRITICAL y HIGH pero no falla ante ellas. Esto permite operar
con `python:3.11-slim` como base, que puede tener vulnerabilidades a nivel de OS fuera
del control del equipo. Se puede endurecer a `exit-code: 1` en fases futuras cuando se
tenga control sobre la imagen base.

**Verificación de salud post-build:** después de construir la imagen, el pipeline levanta
el contenedor con `docker run -d`, espera 3 segundos y llama a `GET /health` con `curl
--fail`. Si el endpoint no responde 200, se imprimen los logs del contenedor y el job
falla. Esto detecta regresiones donde la imagen buildea pero el proceso no arranca
correctamente.

**Registro de contenedores (GHCR):** se usa GitHub Container Registry (`ghcr.io`) como
registry privado. La autenticación usa `GITHUB_TOKEN`, secreto automático de GitHub
Actions que no requiere configuración adicional. Se publican dos tags por push: `latest`
y `:<git-sha>`, lo que permite rollback a cualquier commit. El push se condiciona con
`if: github.event_name == 'push'` para que los PRs solo buildeen y verifiquen sin
publicar imágenes innecesarias.

---

## Estrategia de testing

### Organización de los tests

Los tests se organizan por responsabilidad en archivos independientes dentro de
`api/tests/`:

| Archivo | Responsabilidad | Tests |
|---|---|---|
| `test_health.py` | Verificar que el servicio está vivo | 1 |
| `test_auth.py` | Validar el middleware de autenticación de forma aislada | 3 |
| `test_wells.py` | Validar el endpoint `/api/v1/wells` | 5 |
| `test_forecast.py` | Validar el endpoint `/api/v1/forecast` | 7 |

**Total: 16 tests**

### Decisiones de implementación de los tests

**Mock de API key con `unittest.mock.patch`:** en lugar de setear `os.environ` antes de
importar la app (frágil y dependiente del orden de imports), se usa
`patch("app.core.security.API_KEY", value)` como fixture `autouse=True`. Esto garantiza
que cada test corre con la API key correcta independientemente del orden de ejecución.

**`TestClient` de FastAPI/Starlette:** simula requests HTTP completos en memoria sin
abrir ningún puerto. Los tests son rápidos y no dependen de que el servidor esté corriendo.

**Principio de responsabilidad única por test:** cada test verifica exactamente una
condición (status code, estructura de respuesta, tipo de dato). Esto facilita el
diagnóstico cuando un test falla.

**Casos cubiertos:**
- Autenticación: ausencia de API key, API key inválida, API key válida.
- Validación de parámetros: parámetros faltantes (422), tipos incorrectos (422).
- Lógica de negocio: pozo inexistente (404), rango de fechas inválido (422),
  fecha futura en `date_query` (422).
- Comportamiento del modelo mock: verificación de que la producción es decreciente
  día a día (refleja la lógica de tendencia lineal implementada).

---

## Consecuencias

- Cada PR muestra el resultado de los 16 tests directamente en la interfaz de GitHub.
- No es posible mergear código roto si se configura la branch protection con
  "require status checks to pass".
- El pipeline sirve como documentación ejecutable del comportamiento esperado del sistema.
- Los tests de `forecast` y `wells` también validan indirectamente la capa de servicios
  y los schemas de respuesta, dando cobertura end-to-end sin necesidad de mocks
  adicionales.
- Cada push a `develop` o `main` publica automáticamente una imagen versionada en GHCR,
  disponible como `ghcr.io/<owner>/oil-forecast-api:<sha>`. Esto habilita trazabilidad
  completa entre commits y artefactos desplegados.
- El escaneo con Trivy en modo informativo agrega visibilidad sobre el estado de
  seguridad de la imagen sin bloquear el desarrollo. Las vulnerabilidades detectadas
  aparecen en el log del job `build`.
