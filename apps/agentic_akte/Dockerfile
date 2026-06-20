FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/agentic_akte/ apps/agentic_akte/

ENV PYTHONPATH=/app \
    BRAINFUMP_DATA=/data \
    PORT=8010
VOLUME /data
EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/health')"

CMD ["python", "apps/agentic_akte/server.py"]
