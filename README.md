# Oil Production Forecasting Platform

Plataforma Predictiva de Producción de Hidrocarburos.

Este proyecto consiste en el desarrollo de una API REST capaz de exponer
funcionalidades relacionadas con la predicción de producción de hidrocarburos.

El objetivo del sistema es simular una plataforma backend que permita
consultar y procesar datos de producción para generar estimaciones
predictivas mediante modelos analíticos.

Este repositorio forma parte del trabajo práctico de la materia
Ingeniería de Software.

---

# Integrantes

- Michanie Micol
- Pettazi Valentino
- Castro Tomás

---

# Estructura del Proyecto

El repositorio está organizado de la siguiente forma:

oil-production-forecasting-platform
│
├── api
│ ├── app
│ │ ├── middleware
│ │ ├── models
│ │ ├── routes
│ │ ├── services
│ │ ├── schemas
│ │ └── main.py
│ │
│ ├── requirements.txt
│ ├── README.md
│ └── .gitignore
│
├── docs
│ Documentación del proyecto, diagramas y decisiones de arquitectura.
│
├── tests
│ Tests automatizados del sistema.
│
├── scripts
│ Scripts auxiliares para automatización o utilidades del proyecto.
│
└── README.md
 Documentación general del repositorio.


## Descripción de Componentes

### api

Contiene la implementación del backend del sistema.

Dentro de `api/app`:

- **routes**  
  Define los endpoints de la API.

- **models**  
  Representación de entidades del dominio.

- **services**  
  Lógica de negocio y procesamiento de datos.

- **schemas**  
  Definición de estructuras de request/response de la API.

- **middleware**  
  Componentes que interceptan requests/responses HTTP.

- **main.py**  
  Punto de entrada de la aplicación FastAPI.

---

# Workflow de Desarrollo

El proyecto utiliza un modelo de trabajo basado en **GitFlow**.

## Branches principales

- **main**  
  Contiene la versión estable del proyecto.

- **develop**  
  Rama de integración donde se combinan las nuevas funcionalidades.

---

## Branches de desarrollo

Cada funcionalidad debe desarrollarse en una rama independiente.

Formato:
feature/<nombre-feature>

Ejemplos: 
feature/api-endpoints
feature/prediction-service
feature/documentation


---

## Flujo de trabajo

1. Actualizar la rama develop
git checkout develop
git pull

2. Crear una nueva feature branch
git checkout -b feature/nombre-feature

3. Realizar los cambios y commits

4. Subir la rama al repositorio
git push origin feature/nombre-feature


5. Crear un **Pull Request hacia develop**

---

# Ejecución del Proyecto

1. Crear entorno virtual
python -m venv venv+


2. Activar entorno

Windows:
venv\Scripts\activate


3. Instalar dependencias
pip install -r requirements.txt


4. Ejecutar servidor
uvicorn app.main:app --reload


5. Acceder a la documentación automática
http://localhost:8000/docs


---

# Tecnologías Utilizadas

- Python
- FastAPI
- Uvicorn