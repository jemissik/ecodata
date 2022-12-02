import pytest
import panel as pn
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
    yield server
    try:
        server.stop()
    except AssertionError:
        pass  # tests may already close this
