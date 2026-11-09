FROM kong:3.9

USER root

COPY kong.template.yml /kong/declarative/kong.template.yml
COPY entrypoint.sh /entrypoint.sh

RUN sed -i 's/\r$//' /entrypoint.sh && chmod 0755 /entrypoint.sh \
    && chown kong:kong /kong/declarative/kong.template.yml

USER kong

ENV KONG_DATABASE="off" \
    KONG_DECLARATIVE_CONFIG="/tmp/kong.yml" \
    KONG_ADMIN_LISTEN="off" \
    KONG_PROXY_ACCESS_LOG="/dev/stdout" \
    KONG_ADMIN_ACCESS_LOG="/dev/stdout" \
    KONG_PROXY_ERROR_LOG="/dev/stderr" \
    KONG_ADMIN_ERROR_LOG="/dev/stderr" \
    KONG_NGINX_HTTP_CLIENT_MAX_BODY_SIZE="6m" \
    PORT="8000"

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["kong", "docker-start"]
