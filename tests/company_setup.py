"""A company setup for tests (#385): the body a developer sends to create Musterfirma, a company
made in the app, its evidence two policies and one record of the sample library."""

from typing import Any

POLICIES = ["authentication-standard", "backup-policy"]
RECORDS = ["sign-in-settings-record"]
MUSTERFIRMA: dict[str, Any] = {
    "company": "Musterfirma Süd GmbH",
    "country": "Germany",
    "answer_language": "en",
    "protection_level": "normal",
    "evidence": {"policies": POLICIES, "records": RECORDS},
}
MUSTERFIRMA_ID = "musterfirma-sud-gmbh"
