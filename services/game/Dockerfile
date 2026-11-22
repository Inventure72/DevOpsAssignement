FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS python-base

FROM python-base AS sql-driver
RUN apt-get update && apt-get install -y --no-install-recommends \
    bzip2 curl gcc libc6-dev libssl-dev make && rm -rf /var/lib/apt/lists/*
COPY tools/build_pymssql.sh /tmp/build_pymssql.sh
RUN bash /tmp/build_pymssql.sh /wheels

FROM python-base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/game/requirements.txt /tmp/requirements.txt
COPY shared/container-constraints.txt /tmp/constraints.txt
RUN --mount=type=bind,from=sql-driver,source=/wheels,target=/wheels \
    python -m pip install --no-cache-dir --only-binary=:all: /wheels/pymssql-*.whl -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY shared/*.py shared/
COPY services/__init__.py services/
COPY services/game/*.py services/game/
COPY services/game/domain/*.py services/game/domain/
COPY services/game/migrations/*.sql services/game/migrations/

USER 10001:10001
EXPOSE 8002
CMD ["python", "-m", "services.game"]
