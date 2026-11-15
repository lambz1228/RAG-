FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BIOKB_DATA_DIR=/app/data \
    BIOKB_HOST=0.0.0.0 \
    BIOKB_PORT=5000
WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 biokb \
    && useradd --uid 10001 --gid biokb --no-create-home biokb \
    && mkdir -p /app/data \
    && chown -R biokb:biokb /app/data
USER 10001:10001
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:5000/api/health',timeout=3)); assert r['status']=='ok'"
CMD ["python", "-m", "biokb", "serve"]
