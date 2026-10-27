#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


# Global config

DEBUG = False
# A placeholder: amaGama warns at startup until you set your own.
SECRET_KEY = "foobar"
ENABLE_WEB_UI = False
ENABLE_DATA_ALTERING_API = False


# Database config

DB_NAME = "amagama"
DB_USER = "postgres"
DB_PASSWORD = ""
#DB_HOST = "localhost"
#DB_PORT = "5432"


# Database pool config

DB_MIN_CONNECTIONS = 2
DB_MAX_CONNECTIONS = 20


# Cache config
#
# Used to cache source-id lookups (see Flask-Caching for all available
# options). Defaults to a simple per-process cache; for a multi-process
# deployment a shared backend gives a better hit rate, e.g.:
#CACHE_TYPE = "RedisCache"
#CACHE_REDIS_URL = "redis://localhost:6379/0"


# Updater config
#
# The sources file (None: the one in amagama/updater) and where fetched
# translations are kept.
UPDATER_SOURCES = None
UPDATER_CACHE_DIR = "~/.cache/amagama-updater"
# Parallel import processes.
UPDATER_WORKERS = 4
# Blocks fetched for the first time in one run (None: no limit).
UPDATER_FIRST_FETCHES = 100
# The blocks this server builds: those with any of UPDATER_TAGS (None: all)
# and none of UPDATER_EXCLUDE_TAGS.
UPDATER_TAGS = None
UPDATER_EXCLUDE_TAGS = []


# Levenshtein config

MAX_LENGTH = 2000
MIN_SIMILARITY = 70
MAX_CANDIDATES = 5
