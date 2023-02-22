import requests
import pytest
from sys import platform

@pytest.mark.skipif(platform == "linux" or platform == "linux2", reason = "needs update")
def test_server_has_every_app(serve_apps, port, apps):
    for app in apps:
        r = requests.get(f"http://localhost:{port}/{app}")
        assert 200 <= int(r.status_code) <= 299


# TODO you could add tests using playwright to doing ui browser testing?
