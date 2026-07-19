FROM python:3.12-slim
RUN pip install --no-cache-dir confluent-kafka
COPY bridge.py /app/bridge.py
WORKDIR /app
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD pgrep -f bridge.py || exit 1
CMD ["python", "bridge.py"]
