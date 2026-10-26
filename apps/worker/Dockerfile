FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/requirements.txt .
COPY apps/worker/requirements.txt ./worker-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r worker-requirements.txt
COPY apps/api/app ./app
COPY apps/worker/worker ./worker
CMD ["python", "-m", "worker.main"]
