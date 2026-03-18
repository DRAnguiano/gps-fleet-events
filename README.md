# Chatbot RAG PDF

Este proyecto implementa un sistema de **chatbot RAG (Retrieval-Augmented Generation)** dockerizado para interactuar mediante un bot de Telegram. Además de responder preguntas basadas en documentos PDF cargados, el sistema **recibe y procesa el estatus de unidades de transporte**. Al no contar con una API del proveedor, procesa alertas de eventos desde Gmail y realiza una ingesta en la base de datos aplicando reglas inteligentes, lo que permite generar, por ejemplo, un *score de anomalías de combustible*. Utiliza **FastAPI** para la API, **ChromaDB** como base de datos vectorial, **Ollama** para el modelo de lenguaje (LLM local), y **n8n** para la orquestación de flujos de trabajo.

##  Características

-   **RAG con PDFs**: Carga y consulta de documentos PDF.
-   **Modelo Local**: Usa modelos Open Source vía Ollama (ej. `phi3:mini`) para privacidad y control.
-   **Ingesta y Parseo desde Gmail**: Análisis automatizado de alertas de eventos recibidas por correo electrónico para obtener el estatus de las unidades.
-   **Reglas Inteligentes y Análisis**: La base de datos aplica reglas inteligentes y calcula un *score* de anomalías de combustible basado en los datos ingeridos.
-   **Persistencia**: ChromaDB almacena los embeddings de forma persistente.
-   **API REST**: Endpoints claros para indexar, consultar y verificar salud.
-   **Integración n8n**: Flujo de trabajo automatizado para conectar con Telegram y CRM (Kommo).
-   **Soporte GPU**: Configurado para utilizar NVIDIA GPU si está disponible.

##  Requisitos Previos

-   **Docker** y **Docker Compose** instalados.
-   **(Opcional) NVIDIA GPU** con drivers y `nvidia-container-toolkit` para aceleración.

##  Instalación y Despliegue

1.  **Clonar el repositorio**:
    ```bash
    git clone <url-del-repo>
    cd chatbot-rag-pdf
    ```

2.  **Configurar Variables de Entorno**:
    Crea o modifica el archivo `.env` en la raíz (ver `settings.py` para defaults). Ejemplo básico incluido en el repo.

    **Variables Clave en `.env`**:
    -   `MODEL_NAME`: Modelo LLM a usar (ej. `phi3:mini`).
    -   `EMBEDDING_MODEL`: Modelo para embeddings (ej. `sentence-transformers/all-MiniLM-L6-v2`).
    -   `OLLAMA_HOST`: URL del servicio Ollama (por defecto `http://ollama:11434` dentro de Docker).
    -   `NGROK_AUTHTOKEN` y `NGROK_DOMAIN`: Para exponer n8n públicamente.
    -   `TELEGRAM_BOT_TOKEN`: Token de tu bot de Telegram.

3.  **Preparar Datos**:
    Coloca tus archivos PDF en la carpeta `data/`.

4.  **Iniciar Servicios**:
    ```bash
    docker-compose up -d --build
    ```
    Esto levantará:
    -   `api`: El servidor FastAPI (puerto 8000).
    -   `ollama`: El servidor de modelos LLM (puerto 11434).
    -   `n8n`: Automatización de flujos (puerto 5678).
    -   `ngrok`: Túnel para exponer n8n (puerto 4040).

##  Uso de la API

### 1. Verificar Estado
-   **GET** `/health`
-   Respuesta: `{"status": "ok"}`

### 2. Indexar Documentos
Procesa los archivos en `data/` y crea los embeddings.
-   **POST** `/reindex`
-   Body (opcional):
    ```json
    {
      "top_k": 4
    }
    ```

### 3. Hacer una Pregunta (RAG + LLM)
Recupera contexto y genera una respuesta con el LLM.
-   **POST** `/ask`
-   Body:
    ```json
    {
      "q": "¿De qué trata el documento de póliza?",
      "top_k": 4
    }
    ```

### 4. Consultar (Solo Retrieval)
Devuelve los fragmentos de texto más relevantes sin pasar por el LLM.
-   **POST** `/search`
-   Body:
    ```json
    {
      "q": "términos de servicio",
      "top_k": 4
    }
    ```

##  Flujo de n8n (Integración Telegram)

El archivo `N8N-KOMMO WORKFLOW.json` contiene el flujo para importar en n8n:
1.  **Telegram Trigger**: Escucha mensajes entrantes.
2.  **Lógica**:
    -   Normaliza datos del usuario.
    -   Busca o crea el contacto en **Kommo CRM**.
    -   Envía la consulta a la API RAG (`/query` o `/ask`).
    -   Devuelve la respuesta a Telegram.

Para usarlo:
1.  Accede a n8n en `http://localhost:5678`.
2.  Configura tus credenciales (Telegram, Kommo).
3.  Importa el JSON del flujo.
4.  Activa el workflow.

## Recuperación de Datos y Backfill (Gmail)

Dado que no se dispone de la API directa del proveedor, el sistema aprovecha el histórico de eventos enviados por correo (alertas) para construir y alimentar la base de conocimiento usando un modelo de recuperación manual.

-   Ejecuta el script `./run_backfill.sh` en la raíz del proyecto para iniciar la tarea de recuperación.
-   El script utiliza los módulos contenidos en las carpetas `scripts/` y `shared/` para descargar, parsear la información e ingerirla en nuestra base de datos.
-   Durante la ingesta se evalúan las **reglas inteligentes** (como el *score de anomalías de combustible*), dejando la data lista para ser consumida y consultada de manera eficiente por el bot de Telegram de RAG-LLM.

##  Estructura del Proyecto

```
.
├── app/
│   ├── app.py           # Punto de entrada FastAPI
│   ├── indexer.py       # Lógica RAG (LlamaIndex, Chroma)
│   ├── settings.py      # Configuración y carga de variables
│   └── persona_config.py # Prompt del sistema y personalidad
├── data/                # Carpeta para tus PDFs
├── chroma_db/           # Persistencia de ChromaDB
├── n8n/                 # Datos de n8n
├── scripts/             # Scripts para procesamiento de correos y utilidades
├── shared/              # Recursos y dependencias compartidas para la ingesta
├── run_backfill.sh      # Script bash de ejecución para recuperación de correos
├── .env                 # Variables de entorno
├── docker-compose.yml   # Orquestación de contenedores
├── Dockerfile           # Imagen de la API
└── requirements.txt     # Dependencias Python
```
