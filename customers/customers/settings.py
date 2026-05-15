"""
Django settings for customers project.
"""

from pathlib import Path
import os
from mongoengine import connect

# -----------------------------
# Paths
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

# -----------------------------
# Security
# -----------------------------
SECRET_KEY = 'django-insecure-2*+zf8!e-c)2!_4x2(qreq=+ot1p(ssrzgbcggn32=$gy1!zic'
DEBUG = True
ALLOWED_HOSTS = ['*']

# -----------------------------
# Applications
# -----------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'drf_yasg',
    'api',
    'corsheaders',
]

MIDDLEWARE = [
    'customers.middleware.APILifetimeMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'corsheaders.middleware.CorsMiddleware',
]

ROOT_URLCONF = 'customers.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ============================================================
# CACHE
# Works on both Windows (runserver) and Linux (Docker)
# Set TILE_CACHE_DIR env var in docker-compose to override.
# ============================================================
_TILE_CACHE_DIR = os.environ.get(
    "TILE_CACHE_DIR",
    r"C:\tile_cache" if os.name == "nt" else "/tmp/tile_cache"   # auto-detect OS
)
os.makedirs(_TILE_CACHE_DIR, exist_ok=True)   # create the folder if it doesn't exist

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": _TILE_CACHE_DIR,
        "OPTIONS": {
            "MAX_ENTRIES": 200000,
        },
    }
}

# ============================================================
# LARGE FILE UPLOAD SETTINGS
# ============================================================
FILE_UPLOAD_HANDLERS = [
    'django.core.files.uploadhandler.TemporaryFileUploadHandler',
]
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024        # 2 MB — anything above goes to disk

# Max total POST body — must cover your largest file upload
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024 * 1024   # 50 GB

# Cross-platform temp dir for uploads
_UPLOAD_TEMP_DIR = os.environ.get(
    "UPLOAD_TEMP_DIR",
    r"C:\Temp\django_uploads" if os.name == "nt" else "/tmp/django_uploads"
)
os.makedirs(_UPLOAD_TEMP_DIR, exist_ok=True)
FILE_UPLOAD_TEMP_DIR = _UPLOAD_TEMP_DIR

# ============================================================

WSGI_APPLICATION = 'customers.wsgi.application'
CORS_ALLOW_ALL_ORIGINS = True

# -----------------------------
# MongoDB — host/port from env
# • Local runserver  → defaults to localhost:27017
# • Docker           → set MONGO_HOST=mongo (service name) in docker-compose.yml
# -----------------------------
MONGO_HOST = os.environ.get("MONGO_HOST", "localhost")
MONGO_PORT = int(os.environ.get("MONGO_PORT", 27017))
MapMONGO_HOST = os.environ.get("MONGO_HOST", "122.175.12.225")
MapMONGO_PORT = int(os.environ.get("MONGO_PORT", 27017))
connect(db='map_storage_india_v3',host=f"mongodb://{MapMONGO_HOST}:{MapMONGO_PORT}/map_storage_india_v3?directConnection=true",alias='Maps')
# Map tile settings — used by offlinemap.py
MAX_ZOOM       = 14
TILE_CACHE_TTL = 60 * 60 * 24 * 30   # 30 days

def _mongo(db_name):
    """Build a MongoDB URI using the current MONGO_HOST / MONGO_PORT."""
    return f"mongodb://{MONGO_HOST}:{MONGO_PORT}/{db_name}?directConnection=true"

connect(db='CDR',                 host=_mongo('CDR'),                 alias='cdr_db')
connect(db='CDR_SOURCE',          host=_mongo('CDR_SOURCE'),          alias='source_db')
connect(db='IPDB',                host=_mongo('IPDB'),                alias='ip_info')
connect(db='IPDR',                host=_mongo('IPDR'),                alias='ipdr_db')
connect(db='TowerDump',           host=_mongo('TowerDump'),           alias='tower_dump')
connect(db='TowerDump',           host=_mongo('TowerDump'),           alias='towerdump_db')
connect(db='IP_DB',               host=_mongo('IP_DB'),               alias='ip_db')
connect(db='ssd_logs',            host=_mongo('ssd_logs'),            alias='cell_id')
connect(db='WhatsApp',            host=_mongo('WhatsApp'),            alias='whatsapp_db')
connect(db='SDR_Pro_DB',          host=_mongo('SDR_Pro_DB'),          alias='sdr_db')
connect(db='Watchlist',           host=_mongo('Watchlist'),           alias='watchlist_db')
connect(db='LoginInfo',           host=_mongo('LoginInfo'),           alias='logininfo')
connect(db='ILD_DB',              host=_mongo('ILD_DB'),              alias='ild_db')
# connect(db='map_storage_india_v3',host=_mongo('map_storage_india_v3'),alias='Maps')

# -----------------------------
# Password validation
# -----------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# -----------------------------
# Localization
# -----------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# -----------------------------
# Static files
# -----------------------------
STATIC_URL = 'static/'

# -----------------------------
# Default primary key field type
# -----------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'