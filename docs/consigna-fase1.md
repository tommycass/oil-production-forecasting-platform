# Consigna Fase 1 — Referencia rápida

PDFs completos: `docs/Organización_de_Trabajo___Fase_1.pdf`

---

## Entregables obligatorios

1. **Repositorio Git** con el código del proyecto
2. **README** con instrucciones para ejecutar el sistema
3. **ADRs** documentando decisiones de arquitectura (en `docs/adr/`)
4. **Servicio accesible** por URL pública durante el período de corrección
5. **Video demo de ~5 minutos** mostrando:
   - Funcionamiento de la API
   - Documentación Swagger
   - Monitoreo del sistema
   - Ejecución mediante Docker

---

## Lo que debe estar funcionando

### API REST (Integrante 1 — Micol)
- `GET /api/v1/wells` — listado de pozos, parámetro `date_query`
- `GET /api/v1/forecast` — pronóstico, parámetros `id_well`, `date_start`, `date_end`
- Autenticación via header `X-API-Key: abcdef12345` → 403 si falla
- Datos mock (valores constantes, tendencia lineal o aleatorios)
- Documentación Swagger/OpenAPI accesible online
- Respuestas en formato JSON según spec OpenAPI del enunciado

### Infraestructura y DevOps (Integrante 2 — Tomás)
- `Dockerfile` con runtime, dependencias y código fuente
- `docker compose up` levanta API + Prometheus + Grafana
- Pipeline CI/CD que ejecuta en cada commit/PR:
  1. Instalación de dependencias
  2. Ejecución de tests
  3. Análisis estático de código
  4. Build de imagen Docker
  5. (Opcional) Push a registry privado
- Registro de contenedores privado para almacenar imágenes
- Escaneo de vulnerabilidades en imágenes Docker
- Despliegue automático en entorno de desarrollo
- Verificación automática de salud post-despliegue

### Monitoreo y Docs (Integrante 3 — Valentino)
- Prometheus recolectando métricas de la API
- Grafana con dashboard mostrando:
  - Latencia de requests
  - Cantidad de requests
  - Tasa de errores
  - Disponibilidad del servicio
- ADR-001: elección del framework backend
- ADR-002: uso de Docker
- ADR-003: uso de Prometheus + Grafana
- (Opcional) Alertas ante alta tasa de errores o latencia elevada

---

## Requerimientos no funcionales (del PRD)

- Tiempo de respuesta para generación de pronóstico: **< 5 segundos**
- Disponibilidad de la API REST: **99.5%** en horario operativo
- Dashboard carga en: **< 5 segundos**

---

## Formato de entrega

- URL del servicio accesible durante corrección
- Commit específico del repositorio que se toma para la entrega
- README actualizado con instrucciones para acceder a todos los componentes
- Directorio `docs/adr/` con los ADRs de la entrega
- Video demo de ~5 minutos

---

## Criterios de evaluación

- Cumplimiento de requisitos en tiempo y forma
- Capacidad de responder preguntas sobre diseño y funcionamiento:
  - Supuestos y limitaciones considerados
  - Trade-offs de las alternativas analizadas
  - Justificación técnica de las soluciones desarrolladas
