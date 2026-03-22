# Título: ADR-002: Uso de Docker para contenerización
**Estado:** Aceptado

## Contexto
Necesitamos garantizar que la aplicación, sus bases de datos asociadas y los sistemas de monitoreo puedan ejecutarse de manera confiable e idéntica en diferentes entornos (desarrollo, testing, producción) y por distintos desarrolladores, evitando el problema de dependencias globales incompatibles en la máquina host. Se evaluaron instalaciones nativas y el uso de Máquinas Virtuales pesadas.

## Decisión
Usaremos **Docker y Docker Compose** porque nos permite empaquetar la aplicación y todos sus servicios dependientes (Prometheus, Grafana) en contenedores ligeros y portables, garantizando reproducibilidad en cualquier sistema.

## Consecuencias
**Positivas:**
- Consistencia absoluta entre entornos; lo que funciona en desarrollo funciona en producción.
- Aislamiento de dependencias, evitando conflictos en la máquina local.
- Fácil orquestación de múltiples servicios en la máquina del desarrollador usando `docker-compose up`.

**Negativas:**
- Curva de aprendizaje adicional para construir Dockerfiles eficientes y seguros.
- Mayor consumo de disco y memoria en la máquina local en comparación con instalaciones bare-metal simples.
