# Build context = this folder (operation_service/). Works as a Railway Root Directory
# and with docker-compose (context: ./operation_service).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app" \
    PORT=8002

WORKDIR /app

RUN groupadd --system --gid 1001 appgroup \
    && useradd --system --uid 1001 --gid appgroup --no-create-home appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup . /app/
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod 0755 /app/entrypoint.sh \
    && mkdir -p /app/uploads && chown -R appuser:appgroup /app/uploads

USER appuser

EXPOSE 8002

CMD ["sh", "/app/entrypoint.sh"]
