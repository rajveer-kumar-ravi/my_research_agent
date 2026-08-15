from celery import Celery
from app.core.config import get_settings
import socket
import os
import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

settings = get_settings()

# Sentry initialization for Celery worker process
if getattr(settings, "sentry_dsn", None):
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        integrations=[CeleryIntegration()],
        traces_sample_rate=1.0,
        environment="production",
    )

def is_redis_available():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(('localhost', 6379))
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

# Agar Redis available nahi hai (jaise pytest ke waqt), toh in-memory broker use karo
if is_redis_available() and "PYTEST_CURRENT_TEST" not in os.environ:
    broker_url = settings.redis_url
    backend_url = settings.redis_url
    eager_mode = False
else:
    broker_url = "memory://"
    backend_url = "db+sqlite:///memory_celery.db"
    eager_mode = True

celery_app = Celery(
    "research_worker",
    broker=broker_url,
    backend=backend_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_always_eager=eager_mode,
    task_eager_propagates=eager_mode,
)