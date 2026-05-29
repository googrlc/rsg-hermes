"""Tests for hermes.sync.field_mapper — NowCerts → EspoCRM field transforms."""

from __future__ import annotations

import unittest

from hermes.sync.field_mapper import (
    detect_conflicts,
    map_insured_to_account,
    payload_hash,
)


class MapInsuredToAccountTests(unittest.TestCase):
    def _commercial_insured(self, **overrides) -> dict:
        base = {
            "id": "NC-001",
            "commercialName": "Acme Corp",
            "firstName": "John",
            "lastName": "Doe",
            "insuredType": "Commercial",
            "typeOfBusiness": "LLC",
            "fein": "12-3456789",
            "changeDate": "2026-05-01T10:00:00-05:00",
            "createDate": "2025-01-15T00:00:00",
            "eMail": "john@acme.com",
            "cellPhone": "5551234567",
            "addressLine1": "123 Main St",
            "city": "Atlanta",
            "state": "GA",
            "zipCode": "30301",
        }
        base.update(overrides)
        return base

    def _personal_insured(self) -> dict:
        return {
            "id": "NC-002",
            "firstName": "Jane",
            "lastName": "Smith",
            "insuredType": "Personal",
            "changeDate": "2026-04-20T08:00:00",
        }

    def test_commercial_name_used_for_commercial_type(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["name"], "Acme Corp")

    def test_personal_name_concat(self) -> None:
        result = map_insured_to_account(self._personal_insured())
        self.assertEqual(result["name"], "Jane Smith")

    def test_account_type_enum_map(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["accountType"], "Commercial Lines")

        personal = map_insured_to_account(self._personal_insured())
        self.assertEqual(personal["accountType"], "Personal Lines")

    def test_dedup_key_mapped(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["momentumClientId"], "NC-001")

    def test_date_only_strips_time(self) -> None:
        result = map_insured_to_account(
            self._commercial_insured(), is_first_sync=True,
        )
        self.assertEqual(result["clientSince"], "2025-01-15")

    def test_client_since_omitted_on_subsequent_sync(self) -> None:
        result = map_insured_to_account(
            self._commercial_insured(), is_first_sync=False,
        )
        self.assertNotIn("clientSince", result)

    def test_address_fields_mapped(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["billingAddressStreet"], "123 Main St")
        self.assertEqual(result["billingAddressCity"], "Atlanta")
        self.assertEqual(result["billingAddressState"], "GA")
        self.assertEqual(result["billingAddressPostalCode"], "30301")

    def test_fein_mapped(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["fein"], "12-3456789")

    def test_lead_sources_first_element(self) -> None:
        insured = self._commercial_insured(leadSources=["Website", "Referral"])
        result = map_insured_to_account(insured)
        self.assertEqual(result["referralSource"], "Website")

    def test_append_notes_existing(self) -> None:
        insured = self._commercial_insured(personNotes="New note from AMS")
        existing = {"communicationNotes": "Old CRM note"}
        result = map_insured_to_account(insured, existing_espo=existing)
        self.assertIn("Old CRM note", result["communicationNotes"])
        self.assertIn("New note from AMS", result["communicationNotes"])

    def test_append_notes_no_duplicate(self) -> None:
        insured = self._commercial_insured(personNotes="Same note")
        existing = {"communicationNotes": "Same note"}
        result = map_insured_to_account(insured, existing_espo=existing)
        self.assertEqual(result["communicationNotes"], "Same note")

    def test_default_account_type_when_missing(self) -> None:
        insured = {"id": "NC-X", "commercialName": "Test", "changeDate": "2026-01-01"}
        result = map_insured_to_account(insured)
        self.assertEqual(result["accountType"], "Commercial Lines")

    def test_life_type_maps_to_personal_lines(self) -> None:
        insured = self._personal_insured()
        insured["insuredType"] = "Life"
        result = map_insured_to_account(insured)
        self.assertEqual(result["accountType"], "Personal Lines")


class DetectConflictsTests(unittest.TestCase):
    def test_no_conflict_when_values_match(self) -> None:
        source = {"name": "Acme Corp", "fein": "123"}
        existing = {"name": "Acme Corp", "fein": "123"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 0)

    def test_detects_value_mismatch(self) -> None:
        source = {"name": "Acme Corp", "fein": "123"}
        existing = {"name": "Acme Industries", "fein": "123"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field_name"], "name")

    def test_ignores_configured_fields(self) -> None:
        source = {"momentumLastSynced": "2026-05-01", "name": "X"}
        existing = {"momentumLastSynced": "2026-04-01", "name": "X"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 0)

    def test_skips_when_either_value_is_none(self) -> None:
        source = {"name": "Acme", "fein": None}
        existing = {"name": "Acme", "fein": "123"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 0)


class PayloadHashTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        data = {"name": "Acme", "id": 1}
        self.assertEqual(payload_hash(data), payload_hash(data))

    def test_different_data_different_hash(self) -> None:
        self.assertNotEqual(
            payload_hash({"a": 1}),
            payload_hash({"a": 2}),
        )

    def test_key_order_irrelevant(self) -> None:
        self.assertEqual(
            payload_hash({"b": 2, "a": 1}),
            payload_hash({"a": 1, "b": 2}),
        )


if __name__ == "__main__":
    unittest.main()
