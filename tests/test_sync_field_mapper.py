"""Tests for hermes.sync.field_mapper — NowCerts → EspoCRM field transforms."""

from __future__ import annotations

import unittest

from hermes.sync.field_mapper import (
    detect_conflicts,
    map_insured_to_account,
    map_nowcerts_policy_to_espo_policy,
    map_policy_to_opportunity,
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
        self.assertEqual(result["account_type"], "Commercial Lines")

        personal = map_insured_to_account(self._personal_insured())
        self.assertEqual(personal["account_type"], "Personal Lines")

    def test_dedup_key_mapped(self) -> None:
        result = map_insured_to_account(self._commercial_insured())
        self.assertEqual(result["momentum_client_id"], "NC-001")

    def test_date_only_strips_time(self) -> None:
        result = map_insured_to_account(
            self._commercial_insured(), is_first_sync=True,
        )
        self.assertEqual(result["client_since"], "2025-01-15")

    def test_client_since_omitted_on_subsequent_sync(self) -> None:
        result = map_insured_to_account(
            self._commercial_insured(), is_first_sync=False,
        )
        self.assertNotIn("client_since", result)

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
        self.assertEqual(result["referral_source"], "Website")

    def test_append_notes_existing(self) -> None:
        insured = self._commercial_insured(personNotes="New note from AMS")
        existing = {"communication_notes": "Old CRM note"}
        result = map_insured_to_account(insured, existing_espo=existing)
        self.assertIn("Old CRM note", result["communication_notes"])
        self.assertIn("New note from AMS", result["communication_notes"])

    def test_append_notes_no_duplicate(self) -> None:
        insured = self._commercial_insured(personNotes="Same note")
        existing = {"communicationNotes": "Same note"}
        result = map_insured_to_account(insured, existing_espo=existing)
        self.assertEqual(result["communication_notes"], "Same note")

    def test_default_account_type_when_missing(self) -> None:
        insured = {"id": "NC-X", "commercialName": "Test", "changeDate": "2026-01-01"}
        result = map_insured_to_account(insured)
        self.assertEqual(result["account_type"], "Commercial Lines")

    def test_life_type_maps_to_personal_lines(self) -> None:
        insured = self._personal_insured()
        insured["insuredType"] = "Life"
        result = map_insured_to_account(insured)
        self.assertEqual(result["account_type"], "Personal Lines")

    def test_phone_normalized_to_e164(self) -> None:
        # NowCerts often returns "678-230-5750" style; Espo phoneNumber
        # validator only accepts E.164. This sync used to fail Account
        # writes with validationFailure on phoneNumber for those records.
        for raw, expected in [
            ("678-230-5750", "+16782305750"),
            ("(770) 780-8848", "+17707808848"),
            ("7707808848", "+17707808848"),
            ("17707808848", "+17707808848"),
            ("+17707808848", "+17707808848"),
        ]:
            insured = self._commercial_insured(cellPhone=raw)
            result = map_insured_to_account(insured)
            self.assertEqual(
                result["phoneNumber"],
                expected,
                msg=f"raw {raw!r} did not normalize",
            )

    def test_phone_intl_canonicalized_to_e164(self) -> None:
        # Already-valid international numbers are canonicalized to strict E.164
        # (+ then digits, no spaces/dashes) so Espo's phone validator accepts
        # them instead of rejecting the spaced/dashed raw form.
        insured = self._commercial_insured(cellPhone="+44 20 7946 0958")
        result = map_insured_to_account(insured)
        self.assertEqual(result["phoneNumber"], "+442079460958")

    def test_phone_unnormalizable_is_omitted(self) -> None:
        # Values we cannot turn into E.164 are dropped (field omitted) rather
        # than sent as-is — sending a non-E.164 string fails the whole Account
        # write with validationFailure {field: phoneNumber, type: valid}.
        for bad in ["678-230", "ext 1234", "n/a", "see notes", "555-CALL"]:
            insured = self._commercial_insured(cellPhone=bad)
            result = map_insured_to_account(insured)
            self.assertNotIn(
                "phoneNumber", result, msg=f"un-normalizable {bad!r} should be omitted",
            )


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
        source = {"momentum_last_synced": "2026-05-01", "name": "X"}
        existing = {"momentum_last_synced": "2026-04-01", "name": "X"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 0)

    def test_skips_when_either_value_is_none(self) -> None:
        source = {"name": "Acme", "fein": None}
        existing = {"name": "Acme", "fein": "123"}
        conflicts = detect_conflicts(source, existing)
        self.assertEqual(len(conflicts), 0)


class MapPolicyToOpportunityTests(unittest.TestCase):
    def _policy(self, **overrides) -> dict:
        base = {
            "number": "POL-0001",
            "lineOfBusinesses": [{"lineOfBusinessName": "Personal Auto"}],
            "totalPremium": 1408.0,
            "effectiveDate": "2025-06-01T00:00:00",
            "expirationDate": "2026-06-01T00:00:00",
            "carrierName": "Progressive",
        }
        base.update(overrides)
        return base

    def test_bind_date_effective_date_proposed_all_mirror_eff_date(self) -> None:
        # bindDate, effectiveDate, and proposedEffectiveDate are all
        # layout/workflow-required on Opportunity create — POST 400s with
        # `validationFailure {field: …, type: required}` for each in turn.
        # NowCerts carries one effective date so all three mirror it.
        opp = map_policy_to_opportunity(
            self._policy(), account_id="acct-1", account_name="Acme"
        )
        self.assertIsNotNone(opp)
        self.assertEqual(opp["bindDate"], "2025-06-01")
        self.assertEqual(opp["effectiveDate"], "2025-06-01")
        self.assertEqual(opp["proposedEffectiveDate"], "2025-06-01")

    def test_written_premium_mirrors_amount(self) -> None:
        # writtenPremium is layout-required on Opportunity create — POST 400s
        # without it (validationFailure {field: writtenPremium, type: required}).
        # NowCerts only carries one premium, so the mapper mirrors totalPremium
        # into both amount and writtenPremium.
        opp = map_policy_to_opportunity(
            self._policy(), account_id="acct-1", account_name="Acme"
        )
        self.assertIsNotNone(opp)
        self.assertEqual(opp["amount"], 1408.0)
        self.assertEqual(opp["writtenPremium"], 1408.0)

    def test_no_premium_no_written_premium(self) -> None:
        # If NowCerts didn't carry premium at all, we shouldn't fabricate
        # a writtenPremium = 0 — let the POST fail loudly so a human sees it.
        opp = map_policy_to_opportunity(
            self._policy(totalPremium=None, premium=None, Premium=None),
            account_id="acct-1",
            account_name="Acme",
        )
        self.assertIsNotNone(opp)
        self.assertNotIn("amount", opp)
        self.assertNotIn("writtenPremium", opp)


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


class MapNowCertsPolicyToEspoPolicyTests(unittest.TestCase):
    def _policy(self, **overrides) -> dict:
        base = {
            "number": "CPK-99887-01",
            "lineOfBusinesses": [{"lineOfBusinessName": "Commercial Auto"}],
            "effectiveDate": "2026-07-01T00:00:00",
            "expirationDate": "2027-07-01T00:00:00",
            "totalPremium": 8421.50,
            "carrierName": "Progressive Commercial",
            "businessType": "Renewal",
            "status": "Active",
            "insuredCommercialName": "Summit HVAC LLC",
        }
        base.update(overrides)
        return base

    def test_field_names_match_espo_policy_mixed_casing(self):
        # Casing is the silent-drop trap: these names must match the live entity.
        p = map_nowcerts_policy_to_espo_policy(self._policy(), account_id="ACC123")
        self.assertEqual(p["policy_number"], "CPK-99887-01")
        self.assertEqual(p["line_of_business"], "Commercial Auto")
        self.assertEqual(p["effective_date"], "2026-07-01")
        self.assertEqual(p["expiration_date"], "2027-07-01")
        self.assertEqual(p["premium_amount"], 8421.50)
        self.assertEqual(p["carrier"], "Progressive Commercial")
        self.assertEqual(p["business_type"], "Renewal")
        self.assertEqual(p["status"], "Active")
        self.assertEqual(p["accountId"], "ACC123")  # camelCase link field

    def test_no_policy_number_returns_none(self):
        self.assertIsNone(map_nowcerts_policy_to_espo_policy({"lineOfBusinesses": []}))

    def test_account_id_omitted_when_not_matched(self):
        # No account → never emit accountId (we never create accounts).
        self.assertNotIn("accountId", map_nowcerts_policy_to_espo_policy(self._policy()))

    def test_scalar_lob_and_alt_premium_fields(self):
        p = map_nowcerts_policy_to_espo_policy(
            self._policy(lineOfBusinesses=None, LineOfBusinessName="Workers Comp",
                         totalPremium=None, Premium=1200)
        )
        self.assertEqual(p["line_of_business"], "Workers Comp")
        self.assertEqual(p["premium_amount"], 1200.0)


if __name__ == "__main__":
    unittest.main()
