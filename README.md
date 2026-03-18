# Chatbot RAG PDF

Este proyecto implementa un sistema de **chatbot RAG (Retrieval-Augmented Generation)** capaz de responder preguntas basadas en documentos PDF cargados. Utiliza **FastAPI** para la API, **ChromaDB** como base de datos vectorial, **Ollama** para el modelo de lenguaje (LLM), y **n8n** para la orquestación de flujos de trabajo (Telegram habilitado).

##  Características

-   **RAG con PDFs**: Carga y consulta de documentos PDF.
-   **Modelo Local**: Usa modelos Open Source vía Ollama (ej. `phi3:mini`) para privacidad y control.
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
├── .env                 # Variables de entorno
├── docker-compose.yml   # Orquestación de contenedores
├── Dockerfile           # Imagen de la API
└── requirements.txt     # Dependencias Python
```
