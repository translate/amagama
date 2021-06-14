FROM alpine:3.7

MAINTAINER Navaneeth Sen <navaneethsen@gmail.com>

# install dependencies
RUN \
 apk add --no-cache python3 postgresql-libs libxslt-dev && \
 apk add --no-cache --virtual .build-deps g++ gcc libc-dev libxslt python3-dev musl-dev postgresql-dev libffi-dev

# create a soft link for python3 as python
RUN \
 ln -s /usr/bin/python3 /usr/bin/python

# upgrade the pip
RUN \
 python -m pip install --upgrade pip

# change working directory
WORKDIR /usr/src/app

# copy files
COPY . .

# install the application specific python libraries
RUN \
 python -m pip install -r ./requirements/recommended.txt --no-cache-dir && \
 apk --purge del .build-deps

# set the environment variables
ENV PYTHONUNBUFFERED 1
ENV PATH "$PATH:./bin"
ENV PYTHONPATH "$PYTHONPATH:."

# set the executable permissions for the scripts to be run on docker start
RUN chmod a+x /usr/src/app/start_app.sh
RUN chmod a+x /usr/src/app/add_translations.sh

# the entry point
EXPOSE 8888
CMD ["/usr/src/app/start_app.sh"]