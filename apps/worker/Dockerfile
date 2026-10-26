FROM node:24-bookworm-slim AS picnic-search
COPY apps/api/picnic-search/package*.json /opt/picnic-search/
RUN npm ci --prefix /opt/picnic-search --omit=dev --ignore-scripts

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=picnic-search /usr/local/bin/node /usr/local/bin/node
COPY --from=picnic-search /opt/picnic-search /opt/picnic-search
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY apps/api/requirements.txt .
COPY apps/worker/requirements.txt ./worker-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r worker-requirements.txt
COPY apps/api/app ./app
COPY apps/worker/worker ./worker
CMD ["python", "-m", "worker.main"]
