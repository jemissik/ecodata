import pytest
import panel as pn
import time
import pymovebank
from pymovebank.app.apps import applications


PORT = [6000]


@pytest.fixture
def port():
    PORT[0] += 1
    return PORT[0]


@pytest.fixture
def apps():
    return applications


@pytest.fixture()
def serve_apps(port, apps):
    server = pn.serve(apps, port=port, threaded=True, show=False)
    time.sleep(1) # Wait for server to start
    yield server
    try:
        server.stop()
    except AssertionError:
        pass  # tests may already close this


@pytest.fixture(scope="session")
def install_test_data():
    pymovebank.install_test_datasets()
