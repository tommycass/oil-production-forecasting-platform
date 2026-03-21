# Título: ADR-001: Elección del framework backend
**Estado:** Aceptado

## Contexto
Necesitamos un framework para desarrollar la API de la plataforma de pronóstico de producción petrolera. El framework debe ser rápido, fácil de utilizar, tener buen soporte para asincronía y facilitar la validación estricta de tipos de datos. Se evaluaron opciones como FastAPI, Flask y Django REST Framework.

## Decisión
Usaremos **FastAPI** porque ofrece un alto rendimiento gracias a su soporte nativo para asincronía y el ecosistema de Starlette. Además, su validación automática de datos usando Pydantic y la autogeneración de documentación OpenAPI nos permite reducir el esfuerzo de desarrollo y mantenimiento.

## Consecuencias
**Positivas:**
- Desarrollo rápido y con menos errores gracias a la validación de tipos automática.
- Alto rendimiento en endpoints asíncronos.
- Documentación OpenAPI automática y siempre actualizada.

**Negativas:**
- El ecosistema de plugins y extensiones puede ser menor comparado con Flask o Django.
- Los desarrolladores deben familiarizarse con los conceptos de asincronía en Python (`async`/`await`) y Pydantic.
