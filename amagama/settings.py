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


# Unit checks config
#
# translate-toolkit's checks that rebuilds flag translations with and
# check_units --ttk runs (None: every one that suits stored translations), and
# the processes running checks (None: one per CPU).
UNIT_CHECKS_TTK = None
UNIT_CHECKS_WORKERS = None
# Checks whose failures rebuilds leave out, besides printf, braces and script:
# one whose sampled failures turned out to be broken translations.
UNIT_CHECKS_LEAVE_OUT = []
# Whether rebuilds suggest lower weights for blocks failing those checks more
# than the rest. Only a suggestion: weights are set in the sources file.
UNIT_CHECKS_SUGGEST_WEIGHTS = False


# Levenshtein config

MAX_LENGTH = 2000
MIN_SIMILARITY = 70
MAX_CANDIDATES = 5
