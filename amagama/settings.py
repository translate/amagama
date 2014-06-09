# Global config

DEBUG = False
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


# Levenshtein config

MAX_LENGTH = 1000
MIN_SIMILARITY = 70
MAX_CANDIDATES = 5
