FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/agent_layer/ apps/agent_layer/

ENV PYTHONPATH=/app \
    AGENT_DATA=/data \
    VLLM_BASE_URL=http://vllm:8000/v1 \
    AGENT_MODEL=gemini4-31b \
    PORT=8060
VOLUME /data
EXPOSE 8060

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8060/api/health')"

CMD ["python", "apps/agent_layer/server.py"]
