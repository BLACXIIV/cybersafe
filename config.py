import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    # Change this to a long random string before deploying anywhere real.
    SECRET_KEY = os.environ.get("CYBERSAFE_SECRET_KEY", "dev-secret-change-me")
    DATABASE_PATH = os.path.join(BASE_DIR, "database", "cybersafe.db")
    SCHEMA_PATH = os.path.join(BASE_DIR, "database", "schema.sql")
    # Disable in development: when True, users cannot take tests while a voucher
    # is actively connected.
    BLOCK_TESTS_WHEN_ACTIVE = False
