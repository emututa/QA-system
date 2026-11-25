FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.3.5 \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin runner

USER 10001:10001
WORKDIR /work
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
