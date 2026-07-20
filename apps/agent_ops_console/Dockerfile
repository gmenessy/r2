FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/agent_layer/ apps/agent_layer/
COPY apps/agent_ops_console/ apps/agent_ops_console/

ENV PYTHONPATH=/app \
    AGENT_DATA=/data \
    AGENT_SIM=1 \
    PORT=8080
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"

CMD ["python", "apps/agent_ops_console/server.py"]
