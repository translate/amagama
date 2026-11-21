FROM gcr.io/oss-fuzz-base/base-builder-python
# psycopg2 builds against libpq.
RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev zip \
    && rm -rf /var/lib/apt/lists/*
COPY . $SRC/amagama
WORKDIR $SRC/amagama
COPY .clusterfuzzlite/build.sh $SRC/
