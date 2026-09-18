import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    # Change this to a long random string before deploying anywhere real.
    SECRET_KEY = os.environ.get("CYBERSAFE_SECRET_KEY", "dev-secret-change-me")
    DATABASE_PATH = os.path.join(BASE_DIR, "database", "cybersafe.db")
    SCHEMA_PATH = os.path.join(BASE_DIR, "database", "schema.sql")
    # When True, users cannot take tests while a voucher is actively connected.
    BLOCK_TESTS_WHEN_ACTIVE = True
    # Absolute base URL used for links rendered inside captive-portal pages.
    # url_for(_external=True) can't be trusted there: requests arrive via the
    # Pi's NAT redirect carrying the OS captive-portal probe's Host header
    # (e.g. msftconnecttest.com), not the Pi's real address.
    PORTAL_BASE_URL = os.environ.get("CYBERSAFE_PORTAL_BASE_URL", "http://cybersafe.local:8000")
