# Used by Cloud Run continuous deployment (Cloud Build looks for a Dockerfile at the repo root).
# It builds the same image as backend/Dockerfile, which is still used for manual
# `gcloud run deploy --source .` runs from inside the backend/ folder.
FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .

ENV PORT=8080
CMD exec gunicorn --bind :$PORT --workers 2 --threads 4 --timeout 120 main:app
