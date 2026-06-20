FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/memory_house/ apps/memory_house/

ENV PYTHONPATH=/app \
    BRAINFUMP_DATA=/data \
    PORT=8040
VOLUME /data
EXPOSE 8040

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8040/api/health')"

CMD ["python", "apps/memory_house/server.py"]
