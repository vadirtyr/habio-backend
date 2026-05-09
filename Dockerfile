FROM python:3.11-slim

WORKDIR /app

COPY requirements-prod.txt .

RUN pip install --no-cache-dir -r requirements-prod.txt

COPY . .

EXPOSE 8001

RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001}"]