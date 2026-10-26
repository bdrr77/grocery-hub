FROM node:24-bookworm-slim AS picnic-search
COPY picnic-search/package*.json /opt/picnic-search/
RUN npm ci --prefix /opt/picnic-search --omit=dev --ignore-scripts

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=picnic-search /usr/local/bin/node /usr/local/bin/node
COPY --from=picnic-search /opt/picnic-search /opt/picnic-search
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
