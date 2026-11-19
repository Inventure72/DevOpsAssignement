FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/rooms/requirements.txt /tmp/requirements.txt
COPY shared/container-constraints.txt /tmp/constraints.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt -c /tmp/constraints.txt \
    && install -d -o 10001 -g 10001 -m 0700 /run/repeat/admin /run/repeat/rooms /run/repeat/game

COPY shared/*.py shared/
COPY services/__init__.py services/
COPY services/rooms/*.py services/rooms/
COPY services/rooms/domain/*.py services/rooms/domain/
COPY services/rooms/migrations/*.sql services/rooms/migrations/
COPY catalog/bundled_demo/demo_catalog.json catalog/bundled_demo/
COPY catalog/bundled_demo/clips/*.mp3 catalog/bundled_demo/clips/
COPY tools/__init__.py tools/compose_bootstrap.py tools/setup_rooms_db.py tools/
COPY db/init.sql db/

USER 10001:10001
EXPOSE 8001
CMD ["python", "-m", "services.rooms"]
