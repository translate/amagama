#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Settings for the Docker image, read from environment variables."""

import os


def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


DEBUG = _bool_env("AMAGAMA_DEBUG")
# Unset, this stays the settings.py placeholder, which amaGama warns about.
SECRET_KEY = os.environ.get("AMAGAMA_SECRET_KEY", "foobar")
ENABLE_WEB_UI = _bool_env("AMAGAMA_ENABLE_WEB_UI", default=True)
ENABLE_DATA_ALTERING_API = _bool_env("AMAGAMA_ENABLE_DATA_ALTERING_API")

DB_NAME = os.environ.get("DB_NAME", "amagama")
DB_USER = os.environ.get("DB_USER", "amagama")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = os.environ.get("DB_PORT", "5432")

CACHE_TYPE = os.environ.get("CACHE_TYPE", "SimpleCache")
