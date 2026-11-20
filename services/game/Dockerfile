FROM python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY services/game/requirements.txt /tmp/requirements.txt
COPY shared/container-constraints.txt /tmp/constraints.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements.txt -c /tmp/constraints.txt

COPY shared/*.py shared/
COPY services/__init__.py services/
COPY services/game/*.py services/game/
COPY services/game/domain/*.py services/game/domain/
COPY services/game/migrations/*.sql services/game/migrations/

USER 10001:10001
EXPOSE 8002
CMD ["python", "-m", "services.game"]
