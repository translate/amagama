# Runs the test suite against the source tree mounted at /src
# (docker compose run --rm test).
FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git libpq-dev postgresql \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 tester

COPY requirements /tmp/requirements
RUN pip install --no-cache-dir -r /tmp/requirements/dev.txt

# PostgreSQL won't initialise a cluster as root.
USER tester
WORKDIR /src
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/src
CMD ["sh", "-c", "pytest -p no:cacheprovider --postgresql-exec=\"$(ls /usr/lib/postgresql/*/bin/pg_ctl | tail -1)\""]
