# SPDX-License-Identifier: AGPL-3.0-or-later
# Kosha server image. Data lives in the /data volume; back it up.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
# Extra Python packages, e.g. the PostgreSQL driver (deploy/docker-compose.postgres.yml sets it)
ARG KOSHA_EXTRA_PACKAGES=""
RUN if [ -n "$KOSHA_EXTRA_PACKAGES" ]; then pip install --no-cache-dir $KOSHA_EXTRA_PACKAGES; fi
COPY sdk/python sdk/python
RUN pip install --no-cache-dir ./sdk/python
COPY server server
COPY web web
COPY addins addins
COPY packs packs
COPY deploy/docker/entrypoint.sh /usr/local/bin/kosha-entrypoint
RUN useradd --system --home /data --shell /usr/sbin/nologin kosha && mkdir -p /data && chown kosha /data
# Port: KOSHA_PORT, else PORT (set by hosting platforms), else 8080.
ENV KOSHA_DATA_DIR=/data KOSHA_HOST=0.0.0.0
# No VOLUME line: some hosting platforms refuse it. Mount a volume at /data (docker-compose.yml does); the
# entrypoint gives a root-owned mount to the kosha user and then runs Kosha without root.
EXPOSE 8080
WORKDIR /app/server
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % (os.environ.get('KOSHA_PORT') or os.environ.get('PORT') or '8080'), timeout=4)"
ENTRYPOINT ["kosha-entrypoint"]
CMD ["python", "-m", "kosha"]
