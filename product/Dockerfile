FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives on a mounted volume, never in the image layer.
ENV DB_PATH=/data/jobmachine.db
VOLUME ["/data"]

EXPOSE 8000
# One worker on purpose: SQLite is one file. See DEPLOY.md.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
