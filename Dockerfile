# Imagem do SERVIDOR (painel + API do agente). Não raspa o TRF4.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY src/ src/
COPY server/ server/
COPY config.yaml .

# DB_PATH e GOOGLE_SA_JSON devem apontar para um volume montado no Easypanel.
EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
