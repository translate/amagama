import os

# Global config

DEBUG = os.getenv('SECRET_KEY', False)
SECRET_KEY = os.getenv('SECRET_KEY', 'foobar')
ENABLE_WEB_UI = os.getenv('ENABLE_WEB_UI', True)
ENABLE_DATA_ALTERING_API = os.getenv('ENABLE_DATA_ALTERING_API', False)

# Database config

DB_NAME = os.getenv('DB_NAME', 'amagama')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', '')
DB_HOST = os.getenv('DB_HOST', '')
DB_PORT = os.getenv('DB_PORT', '')

# Database pool config
DB_MIN_CONNECTIONS = os.getenv('DB_MIN_CONNECTIONS', 2)
DB_MAX_CONNECTIONS = os.getenv('DB_MAX_CONNECTIONS', 20)

# Levenshtein config

MAX_LENGTH = os.getenv('MAX_LENGTH', 2000)
MIN_SIMILARITY = os.getenv('MIN_SIMILARITY', 70)
MAX_CANDIDATES = os.getenv('MAX_CANDIDATES', 5)
