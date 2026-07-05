FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/memory_cockpit/ apps/memory_cockpit/

ENV PYTHONPATH=/app \
    PORT=8050
EXPOSE 8050

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8050/api/health')"

CMD ["python", "apps/memory_cockpit/server.py"]
