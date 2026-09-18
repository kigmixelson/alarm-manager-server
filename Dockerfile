FROM python:3.11-slim-bookworm AS app

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md ./
COPY alarm_manager_server ./alarm_manager_server

RUN pip install --upgrade pip && pip install .

# Check the installed package, not the source tree in /app.
RUN cd /tmp && python -c "import alarm_manager_server.api.app; import alarm_manager_server.worker.run; import alarm_manager_server.plugins.registry; import alarm_manager_server.plugins.oracle_diagnostics; import alarm_manager_server.plugins.oracle_check"

EXPOSE 4800

CMD ["alarm-manager-server"]


# Build with --target oracle-thick for legacy Oracle password verifiers.
FROM app AS oracle-thick
ARG TARGETARCH
ARG ORACLE_IC_URL=https://download.oracle.com/otn_software/linux/instantclient/1932000/instantclient-basic-linux.x64-19.32.0.0.0dbru.zip
ARG ORACLE_IC_SHA256=1749ca1eb5f75b038f2b7ce0abc42eb4575928159259fc4fc851c54a8ca7b002
RUN test "$TARGETARCH" = amd64 \
    && apt-get update \
    && apt-get install -y --no-install-recommends libaio1 unzip curl ca-certificates \
    && curl --fail --location --retry 3 "$ORACLE_IC_URL" -o /tmp/instantclient.zip \
    && echo "$ORACLE_IC_SHA256  /tmp/instantclient.zip" | sha256sum -c - \
    && mkdir -p /opt/oracle \
    && unzip -q /tmp/instantclient.zip -d /opt/oracle \
    && echo /opt/oracle/instantclient_19_32 > /etc/ld.so.conf.d/oracle-instantclient.conf \
    && ldconfig \
    && rm -f /tmp/instantclient.zip \
    && rm -rf /var/lib/apt/lists/*
ENV ORACLE_MODE=thick
RUN python -c "import oracledb; oracledb.init_oracle_client(); assert not oracledb.is_thin_mode(); print(oracledb.clientversion())"

# Preserve the default lightweight image.
FROM app AS thin
