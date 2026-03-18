FROM python:3.11-slim

WORKDIR /app

# deps útiles
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install -r /app/requirements.txt

# código y datos
COPY app /app/app
COPY data /app/data

# DEBUG opcional
RUN echo "==== DEBUG indexer.py (head) ====" \
 && sed -n '1,60p' /app/app/indexer.py \
 && echo "==== END DEBUG ===="

# carpetas de trabajo
RUN mkdir -p /app/chroma_db /app/.cache && chmod -R 777 /app

EXPOSE 8000

CMD ["uvicorn","app.app:app","--host","0.0.0.0","--port","8000"]
