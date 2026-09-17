FROM python:3.11-slim

WORKDIR /app

# aiperf depends on crick, which must be compiled from source on slim images.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt aiperf \
    && apt-get purge -y build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8008

ENV AIPERF_BIN=/usr/local/bin/aiperf

# Replay validation can run for several minutes on larger subsets.
CMD ["gunicorn", "--bind", "0.0.0.0:8008", "--workers", "2", "--threads", "4", "--timeout", "900", "app:app"]
