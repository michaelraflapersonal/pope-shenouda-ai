FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY psai ./psai
# The library is NOT in the image: Cloud Run mounts the private bucket at /data (read-only).
ENV PSAI_DATA=/data PSAI_READONLY=1 PYTHONUNBUFFERED=1
CMD exec uvicorn psai.server:app --host 0.0.0.0 --port ${PORT:-8080}
