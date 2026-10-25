# ---------- build: ставим зависимости в отдельный префикс ----------
FROM python:3.12.8-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN pip install --root-user-action=ignore --prefix=/install -r requirements.txt

# ---------- runtime: только интерпретатор, зависимости и код ----------
FROM python:3.12.8-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DB_PATH=/data/agent.db APP_PORT=8080
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data && chown app:app /data
COPY --from=build /install /usr/local
WORKDIR /app
COPY --chown=app:app app ./app
COPY --chown=app:app eval ./eval
COPY --chown=app:app run.py ./
USER app
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
