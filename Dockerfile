# syntax=docker/dockerfile:1
ARG RUN_IMAGE=python:3.12-slim
FROM $RUN_IMAGE
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG UID=1000
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/apphome" \
    --shell "/sbin/nologin" \
    --uid "${UID}" \
    appuser
COPY app /apphome/app
COPY requirements.txt /apphome/
COPY certs/*.crt /usr/local/share/ca-certificates
RUN update-ca-certificates
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

ARG PIP_INDEX_URL=''
ENV PIP_INDEX_URL=$PIP_INDEX_URL
ARG PIP_TRUSTED_HOST=''
ENV PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST

WORKDIR /apphome
RUN python3 -m pip install -r requirements.txt
ENV PYTHONPATH=/apphome
USER appuser
EXPOSE 8080

ENV HOST='0.0.0.0'
ENV PORT=8080
ENV APP_VERSION='0.9.0'
ENV PROJECT_NAME=fdm-search
ENV OPENAI_EMBEDDING_MODEL=text-embedding-3-small
ENV VECTOR_SIZE=1536

CMD ["python3", "app/main.py"]
