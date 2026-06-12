FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/prompt_optimizer/ apps/prompt_optimizer/

ENV PYTHONPATH=/app \
    BRAINFUMP_DATA=/data \
    PORT=8030
VOLUME /data
EXPOSE 8030

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8030/api/metrics')"

CMD ["python", "apps/prompt_optimizer/server.py"]
