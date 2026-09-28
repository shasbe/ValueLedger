# Built from the repository root so the image can carry both the service and the
# collector — the service hands the collector out to the machines that need it.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY service/app/    ./app/
COPY service/static/ ./static/
COPY collector/      /collector/

# Single worker on purpose: with SQLite on the container filesystem, multiple
# workers race on create_all() at boot and would also race the bootstrap seed.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
