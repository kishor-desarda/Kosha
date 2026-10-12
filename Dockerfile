# SPDX-License-Identifier: AGPL-3.0-or-later
# Kosha server image. Data lives in the /data volume; back it up.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY sdk/python sdk/python
RUN pip install --no-cache-dir ./sdk/python
COPY server server
COPY web web
COPY addins addins
RUN useradd --system --home /data --shell /usr/sbin/nologin kosha && mkdir -p /data && chown kosha /data
USER kosha
ENV KOSHA_DATA_DIR=/data KOSHA_HOST=0.0.0.0 KOSHA_PORT=8080
VOLUME /data
EXPOSE 8080
WORKDIR /app/server
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
CMD ["python", "-m", "kosha"]
