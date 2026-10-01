import dataclasses
import unittest

from evidence import (
    AnalysisConclusion, EvidenceBuilder, EvidenceTrace, HumanConfirm,
    HumanConfirmationLedger, evidence_index, replay_review,
)
from normalizer import DataNormalizer
from providers import ProviderEnvelope, ProviderStatus


RUN = "run-004"
TASK = "task-001"
PATH = f"archive/{RUN}/{TASK}/request-abc/response.json"


def fetched(payload=None, *, raw_path=PATH):
    return ProviderEnvelope(
        provider_name="TUSHARE_MCP", data_kind="historical_daily",
        status=ProviderStatus.AVAILABLE, request_timestamp="2026-10-01T12:00:00Z",
        provider_timestamp="2026-10-01T19:59:00+08:00", raw_reference=raw_path,
        payload=payload or {
            "bars": [{"ts_code": "601518.SH", "trade_date": "20261001", "open": 10,
                      "high": 11, "low": 9, "close": 10.5, "pre_close": 10,
                      "vol": 100, "amount": 5, "adjust_type": "UNADJUSTED"}]
        }, error=None,
    )


def built(*, analysis=False, supersedes=None):
    fetch = fetched()
    normalized = DataNormalizer().normalize(
        fetch, units={"price": "元", "vol": "手", "amount": "万元"}
    )
    output = {"signal": "hold"} if analysis else None
    return EvidenceBuilder().build(
        fetch_result=fetch, endpoint_or_tool="daily", request_params={"stock_code": "601518.SH"},
        collection_task_id=TASK, system_run_id=RUN, normalization_result=normalized,
        model_version="model-v1" if analysis else None, analysis_output=output,
        human_confirm=HumanConfirm.CONFIRMED if analysis else HumanConfirm.PENDING,
        supersedes_evidence_id=supersedes,
    )


class EvidenceBuilderTests(unittest.TestCase):
    def test_builder_assembles_contract_and_hash_sidecar(self):
        record = built(analysis=True)
        base_fields = {
            "source", "endpoint_or_tool", "request_params", "request_timestamp",
            "provider_timestamp", "raw_response_path", "normalization_rule", "validation_result",
            "collection_task_id", "system_run_id", "model_version", "human_confirm",
        }
        hash_fields = {"raw_response_hash", "normalized_data_hash", "analysis_output_hash"}
        names = {field.name for field in dataclasses.fields(record)}
        self.assertTrue(base_fields | hash_fields <= names)
        self.assertTrue(all(getattr(record, name) for name in hash_fields))
        self.assertTrue(record.request_timestamp.endswith("+08:00"))
        self.assertEqual(record.validation_result.status.value, "PASS")

    def test_sensitive_request_params_are_discarded_recursively(self):
        params = {
            "token": "forbidden", "Password": "forbidden", "cookie": "forbidden",
            "session": "forbidden", "api-key": "forbidden", "API Key": "forbidden",
            "stock_code": "601518.SH",
            "range": {"start": "2026-09-01", "API_KEY": "forbidden"},
        }
        record = EvidenceBuilder().build(
            fetch_result=fetched(), endpoint_or_tool="daily", request_params=params,
            collection_task_id=TASK, system_run_id=RUN,
        )
        rendered = repr(dict(record.request_params)).lower()
        for forbidden in ("token", "password", "cookie", "session", "api_key", "api-key", "forbidden"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(record.request_params["stock_code"], "601518.SH")
        self.assertEqual(record.request_params["range"]["start"], "2026-09-01")
        with self.assertRaises(TypeError):
            record.request_params["range"]["start"] = "changed"

    def test_write_failure_is_not_silent(self):
        builder = EvidenceBuilder()
        with self.assertRaises(ValueError):
            builder.build(
                fetch_result=fetched(raw_path="wrong/path.json"), endpoint_or_tool="daily",
                request_params={}, collection_task_id=TASK, system_run_id=RUN,
            )
        self.assertEqual(builder.missing_events[0].collection_task_id, TASK)

    def test_record_is_immutable_and_correction_supersedes(self):
        original = built()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            original.model_version = "changed"
        replacement = built(supersedes=original.evidence_id)
        self.assertEqual(replacement.supersedes_evidence_id, original.evidence_id)
        self.assertNotEqual(replacement.evidence_id, original.evidence_id)
        self.assertFalse(hasattr(EvidenceBuilder, "update"))
        self.assertFalse(hasattr(EvidenceBuilder, "delete"))


class ConfirmationTests(unittest.TestCase):
    def test_all_legal_transitions_and_illegal_transition(self):
        pending = built()
        ledger = HumanConfirmationLedger()
        ledger.transition(pending, HumanConfirm.CONFIRMED)
        ledger.transition(pending, HumanConfirm.REJECTED, reason="manual correction")
        self.assertEqual(ledger.current(pending), HumanConfirm.REJECTED)

        second = built()
        ledger.transition(second, HumanConfirm.REJECTED)
        with self.assertRaises(ValueError):
            ledger.transition(second, HumanConfirm.CONFIRMED)
        self.assertEqual(len(ledger.events), 3)


class TraceAndReviewTests(unittest.TestCase):
    def conclusion(self, evidence_id, **changes):
        values = dict(
            analysis_report_id="report-1", evidence_ids=(evidence_id,),
            structured_fields={"signal": "hold"}, model_version="model-v1",
            calculation={"method": "identity"},
        )
        values.update(changes)
        return AnalysisConclusion(**values)

    def test_trace_drills_down_to_raw_response(self):
        record = built(analysis=True)
        result = EvidenceTrace([record], [self.conclusion(record.evidence_id)]).query("report-1")
        self.assertTrue(result.complete)
        self.assertEqual(result.chain[0]["raw_response_path"], PATH)
        self.assertEqual(result.chain[0]["source"], "TUSHARE_MCP")

    def test_missing_link_fails_closed(self):
        record = built(analysis=True)
        conclusion = self.conclusion(record.evidence_id, calculation=None)
        result = EvidenceTrace([record], [conclusion]).query("report-1")
        self.assertFalse(result.complete)
        self.assertIn("calculation", result.missing)

    def test_replay_matches_and_detects_tampered_raw(self):
        record = built(analysis=True)
        conclusion = self.conclusion(record.evidence_id)
        raw = fetched().payload
        normalized = DataNormalizer().normalize(
            fetched(), units={"price": "元", "vol": "手", "amount": "万元"}
        ).canonical_data
        good = replay_review(
            conclusion, [record], raw_reader=lambda _: raw,
            normalize=lambda value, rules: normalized,
            calculate=lambda rows, model: {"signal": "hold"},
        )
        self.assertTrue(good.consistent)
        bad = replay_review(
            conclusion, [record], raw_reader=lambda _: {**raw, "tampered": True},
            normalize=lambda value, rules: normalized,
            calculate=lambda rows, model: {"signal": "hold"},
        )
        self.assertFalse(bad.consistent)
        self.assertEqual(bad.differences[0].reason, "raw_response_hash mismatch")

    def test_evidence_index_is_exact_subset(self):
        index = evidence_index([built()])[0]
        self.assertEqual(set(index), {
            "source", "request_timestamp", "provider_timestamp", "validation_result", "human_confirm"
        })


if __name__ == "__main__":
    unittest.main()
