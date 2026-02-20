import os

SECRET_KEY = os.getenv("SUPERSET_SECRET_KEY", "transparent-company-superset-secret")
WTF_CSRF_ENABLED = True
SQLALCHEMY_WARN_20 = False
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
}

# Keep defaults lightweight for local MVP demo.
ROW_LIMIT = 5000
