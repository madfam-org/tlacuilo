# syntax=docker/dockerfile:1.7
# tlacuilo landing — a static page served by Caddy, non-root, port 8080. The only
# public surface of the service (the API stays in-cluster). Build context = repo root.
FROM caddy:2-alpine
RUN adduser -D -u 1001 web && mkdir -p /data /config /srv && chown -R 1001:1001 /data /config /srv
COPY web/Caddyfile /etc/caddy/Caddyfile
COPY web/index.html /srv/index.html
ENV XDG_DATA_HOME=/data XDG_CONFIG_HOME=/config
USER 1001
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
