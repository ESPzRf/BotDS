FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-postgres.txt ./
ARG WITH_POSTGRES=0
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$WITH_POSTGRES" = "1" ]; then pip install --no-cache-dir -r requirements-postgres.txt; fi

COPY bot.py config.py ./
COPY core ./core
COPY cogs ./cogs
COPY locales ./locales

RUN mkdir -p /app/data /app/logs

VOLUME ["/app/data"]
CMD ["python", "bot.py"]
