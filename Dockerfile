FROM alpine:3.22 AS sqlite

RUN apk add --no-cache sqlite \
    && addgroup -S monitor \
    && adduser -S -G monitor monitor \
    && mkdir -p /data \
    && chown monitor:monitor /data

USER monitor
WORKDIR /data

ENTRYPOINT ["sqlite3", "/data/monitoring.db"]

FROM sqlite AS app
USER root
RUN apk add --no-cache python3 py3-pip chromium chromium-chromedriver tzdata
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt
ENV PATH="/opt/venv/bin:$PATH" CHROME_BIN=/usr/bin/chromium CHROMEDRIVER_BIN=/usr/bin/chromedriver
WORKDIR /app
COPY partner_monitor /app/partner_monitor
COPY config /app/config
USER monitor
ENTRYPOINT ["python3", "-m", "partner_monitor"]
CMD ["--help"]
