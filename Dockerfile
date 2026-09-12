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
RUN apk add --no-cache python3 py3-requests py3-dotenv py3-openpyxl
WORKDIR /app
COPY partner_monitor /app/partner_monitor
COPY config /app/config
USER monitor
ENTRYPOINT ["python3", "-m", "partner_monitor"]
CMD ["--help"]
