FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies required by evdev
RUN apt-get update && apt-get install -y \
    build-essential \
    linux-headers-generic \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /data

COPY scanner.py .

VOLUME ["/data"]
CMD ["python", "scanner.py"]