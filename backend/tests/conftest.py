"""Select an isolated database before pytest imports any application module."""
import os
import tempfile

_test_directory = tempfile.TemporaryDirectory(prefix="lifeflow-pytest-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_test_directory.name, "test.db").replace("\\", "/")
os.environ.pop("DEMO_API_TOKEN", None)


def pytest_sessionfinish(session, exitstatus):
    from backend.database import engine
    engine.dispose()
    _test_directory.cleanup()
