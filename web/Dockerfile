# syntax=docker/dockerfile:1.7
# tlacuilo landing — a static page served by Caddy, non-root, port 8080. The only
# public surface of the service (the API stays in-cluster). Build context = repo root.
FROM caddy:2-alpine
# The official image gives /usr/bin/caddy the file capability cap_net_bind_service.
# Under the pod's securityContext (capabilities drop ALL + allowPrivilegeEscalation
# false) the kernel refuses to exec a binary carrying file capabilities:
# "exec /usr/bin/caddy: operation not permitted" (CrashLoopBackOff, 2026-09-05).
# We listen on 8080 as uid 1001, so the capability is not needed — strip it.
RUN apk add --no-cache libcap && setcap -r /usr/bin/caddy && apk del libcap \
 && adduser -D -u 1001 web && mkdir -p /data /config /srv && chown -R 1001:1001 /data /config /srv
COPY web/Caddyfile /etc/caddy/Caddyfile
COPY web/index.html /srv/index.html
ENV XDG_DATA_HOME=/data XDG_CONFIG_HOME=/config
USER 1001
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
