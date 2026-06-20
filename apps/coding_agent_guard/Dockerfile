FROM python:3.12-slim

WORKDIR /app
COPY brainfump/ brainfump/
COPY apps/__init__.py apps/__init__.py
COPY apps/coding_agent_guard/ apps/coding_agent_guard/

ENV PYTHONPATH=/app \
    BRAINFUMP_DATA=/data \
    PORT=8020
VOLUME /data
EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/api/health')"

CMD ["python", "apps/coding_agent_guard/server.py"]
