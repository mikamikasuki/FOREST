FROM python:3.11-slim-bookworm AS task
RUN groupadd --gid 10001 forest && useradd --uid 10001 --gid forest --create-home forest
WORKDIR /forest
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLBACKEND=Agg
USER 10001:10001
WORKDIR /workspace
CMD ["python", "--version"]

FROM node:22-bookworm-slim AS web
WORKDIR /build/apps/web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM postgres:17-bookworm AS pgtools

FROM task AS runtime
USER root
RUN apt-get update && apt-get install -y --no-install-recommends texlive-latex-base texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended openssh-client procps postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=pgtools /usr/lib/postgresql/17/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgtools /usr/lib/postgresql/17/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=pgtools /usr/lib/*-linux-gnu/libpq.so.5 /opt/pg-libs/
ENV LD_LIBRARY_PATH=/opt/pg-libs
WORKDIR /forest
COPY --chown=10001:10001 . .
COPY --chown=10001:10001 --from=web /build/apps/web/dist apps/web/dist
RUN mkdir -p /data && chown 10001:10001 /data
ENV FOREST_DATA_DIR=/data
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "/forest/scripts/container_entrypoint.py"]
CMD ["python", "-m", "uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
