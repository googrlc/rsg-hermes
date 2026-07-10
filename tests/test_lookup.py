from __future__ import annotations

from hermes.commands.lookup import handle


class FakeClient:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, entity: str, **kwargs):
        self.calls.append((entity, kwargs.get("params", {})))
        return self.payload


def test_policy_lookup_fetches_full_record_and_keeps_date_fields() -> None:
    client = FakeClient(
        {
            "list": [
                {
                    "id": "p-1",
                    "name": "Atlas Fleet Policy",
                    "policy_number": "WC-2026-001",
                    "line_of_business": "Commercial Auto",
                    "carrier": "Progressive",
                    "effective_date": "2026-01-01",
                    "expiration_date": "2027-01-01",
                    "premium_amount": 12000,
                    "customHeavyField": "keep-me",
                }
            ]
        }
    )

    result = handle(client, "find policy WC-2026-001")

    assert result.ok
    assert "Eff: 2026-01-01" in result.message
    assert "Exp: 2027-01-01" in result.message
    assert result.data["policies"][0]["customHeavyField"] == "keep-me"

    entity, params = client.calls[0]
    assert entity == "Policy"
    assert "select" not in params
    where = params.get("where", [])
    attrs = [rule.get("attribute") for rule in where[0].get("value", [])]
    assert "policy_number" in attrs
    assert "policyNumber" in attrs


def test_account_lookup_fetches_full_record() -> None:
    client = FakeClient({"list": [{"id": "a-1", "name": "Acme", "customField": "full"}]})

    result = handle(client, "find account Acme")

    assert result.ok
    assert result.data["accounts"][0]["customField"] == "full"
    entity, params = client.calls[0]
    assert entity == "Account"
    assert "select" not in params
