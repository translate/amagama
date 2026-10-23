# Global config

DEBUG = False
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


# Levenshtein config

MAX_LENGTH = 2000
MIN_SIMILARITY = 70
MAX_CANDIDATES = 5
