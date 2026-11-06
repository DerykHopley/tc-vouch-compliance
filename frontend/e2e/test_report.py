"""The readiness report's browser checks (#56) run on a Vouch of their own (#234)."""

import json
from urllib.request import urlopen


def test_the_report_server_answers_health(report_url):
    with urlopen(f"{report_url}/api/health", timeout=10) as response:
        assert response.status == 200
        assert json.load(response)["status"] == "ok"
