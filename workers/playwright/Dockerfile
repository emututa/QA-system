FROM mcr.microsoft.com/playwright/python:v1.51.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir playwright==1.51.0 \
    && useradd --system --uid 10001 --create-home runner

WORKDIR /workspace
USER runner
