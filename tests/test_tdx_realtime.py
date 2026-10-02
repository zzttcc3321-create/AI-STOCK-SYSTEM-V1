"""All HTTP boundaries are mocked; no listener or network call is used."""

import ast
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from bridge.contracts import (
    FIELDS, MAX_PACKET_BYTES, freshness, json_bytes, packet_hash, validate_packet,
)
from bridge.ingress import IngressError, IngressStore, handler_for
from bridge.pipeline import ProcessLock, materialize
from bridge.windows_tdx_push import (
    NoRedirect, Outbox, PushHTTPTransport, make_envelope, market_session, run_once,
)
from providers import ProviderStatus, TdxLocalHttpProvider
from providers.tdx_realtime import LocalHTTPTransport

NOW = datetime(2026, 10, 2, 3, 30, tzinfo=timezone.utc)
# Synthetic numbers in the verified 601518.SH response SHAPE. These are not
# the user's observed prices, holdings, or a captured authenticated response.
SNAPSHOT = {
    "Now": 4.2, "LastClose": 4.1, "Open": 4.12, "Max": 4.3, "Min": 4.0,
    "Volume": 12345, "Amount": 5184900, "Average": 4.2,
    "Buyp": [4.19, 0, 0, 0, 0], "Buyv": [200, 0, 0, 0, 0],
    "Sellp": [4.21, 0, 0, 0, 0], "Sellv": [300, 0, 0, 0, 0],
    "Inside": 12, "Outside": 24, "NowVol": 3, "TickDiff": 0,
    "ZAFPre3": 0, "Zangsu": 0, "XsFlag": 0, "ItemNum": 1,
}
TEST_ENV = {"TDX_INGRESS_TOKEN": "TEST_TOKEN_ONLY", "REALTIME_MISSING_AFTER_SECONDS": "1000"}


def setUpModule():
    # Test discovery fails immediately if a new test accidentally uses real TCP.
    global NETWORK_GUARDS
    NETWORK_GUARDS = [patch("socket.socket", side_effect=AssertionError("real network forbidden")),
                      patch("socket.create_connection", side_effect=AssertionError("real network forbidden"))]
    for guard in NETWORK_GUARDS:
        guard.start()


def tearDownModule():
    for guard in reversed(NETWORK_GUARDS):
        guard.stop()


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class FakeLocal:
    def __init__(self, changes=None, failure=None, raw=None):
        self.changes, self.failure, self.raw = changes or {}, failure, raw
        self.calls, self.health_calls = [], []

    def post(self, body, **timeouts):
        self.calls.append((json.loads(body), timeouts))
        if self.failure:
            raise self.failure
        if self.raw is not None:
            return self.raw
        return json_bytes({"jsonrpc": "2.0", "id": json.loads(body)["id"],
                           "result": {"ErrorId": "0", **SNAPSHOT, **self.changes}})

    def health_check(self, **kwargs):
        self.health_calls.append(kwargs)
        return True


def fetch_result(*, key="request_1", clock=None, transport=None):
    local = transport or FakeLocal()
    provider = TdxLocalHttpProvider(local, environ={}, clock=clock or Clock())
    result = provider.fetch({"stock_id": "601518.SH", "data_kind": "realtime_quote", "request_id": key,
                             "system_run_id": "run_1", "collection_task_id": "task_1"})
    return result, local


def packet(*, key="request_1", clock=None, transport=None, mode="AUTO", units=None):
    clock = clock or Clock()
    result, _ = fetch_result(key=key, clock=clock, transport=transport)
    return make_envelope(result, mode=mode, units={} if units is None else units, now=clock(), stale_after=300)


class DirectSender:
    def __init__(self, store, *, lose_ack=False, fail=False):
        self.store, self.lose_ack, self.fail = store, lose_ack, fail
        self.calls = []

    def send(self, envelope):
        self.calls.append(deepcopy(envelope))
        if self.fail:
            raise OSError("TEST_TOKEN_ONLY deliberately in exception; must never be logged")
        ack = self.store.receive("Bearer TEST_TOKEN_ONLY", envelope)
        if self.lose_ack:
            self.lose_ack = False
            raise OSError("acknowledgment lost")
        return ack


class ProviderTests(unittest.TestCase):
    def test_success_mapping_verified_shape_and_contract(self):
        result, local = fetch_result()
        self.assertEqual(result.status, ProviderStatus.AVAILABLE)
        self.assertTrue(TdxLocalHttpProvider(local, environ={}).validate_contract(result).valid)
        expected = {
            "now": 4.2, "last_close": 4.1, "open": 4.12, "high": 4.3, "low": 4.0,
            "volume": 12345, "amount": 5184900, "average": 4.2,
            "bid_prices": SNAPSHOT["Buyp"], "bid_volumes": SNAPSHOT["Buyv"],
            "ask_prices": SNAPSHOT["Sellp"], "ask_volumes": SNAPSHOT["Sellv"],
        }
        self.assertEqual(result.payload["snapshot"], expected)
        self.assertIsNone(result.provider_timestamp)

    def test_exact_rpc_request_and_timeouts(self):
        _, local = fetch_result()
        body, timeouts = local.calls[0]
        self.assertEqual(body, {"jsonrpc": "2.0", "id": "request_1", "method": "get_market_snapshot",
                                "params": {"stock_code": "601518.SH"}})
        self.assertEqual(timeouts, {"connect_timeout": 3, "read_timeout": 10})

    def test_configurable_timeouts(self):
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={"TDX_CONNECT_TIMEOUT": "2", "TDX_READ_TIMEOUT": "7"})
        provider.fetch("601518.SH")
        self.assertEqual(local.calls[0][1], {"connect_timeout": 2, "read_timeout": 7})

    def test_stock_suffixes_and_string_convenience(self):
        for stock_id in ("601518.SH", "000001.SZ", "920001.BJ"):
            self.assertEqual(TdxLocalHttpProvider(FakeLocal(), environ={}).fetch(stock_id).status, ProviderStatus.AVAILABLE)

    def test_errorid_failure_preserves_raw_without_snapshot(self):
        for error_id in ("1", "-1", 0, None):
            with self.subTest(error_id=error_id):
                result, _ = fetch_result(transport=FakeLocal({"ErrorId": error_id}))
                self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
                self.assertEqual(result.payload["realtime_quote_status"], "MISSING")
                self.assertIsNone(result.payload["snapshot"])
                self.assertIsNotNone(result.payload["raw_response_hash"])

    def test_tdx_exit_and_timeout_are_missing_no_retry(self):
        for failure, expected in ((ConnectionRefusedError(), ProviderStatus.UNAVAILABLE),
                                  (TimeoutError(), ProviderStatus.TIMEOUT)):
            local = FakeLocal(failure=failure)
            result, _ = fetch_result(transport=local)
            self.assertEqual(result.status, expected)
            self.assertEqual(result.payload["realtime_quote_status"], "MISSING")
            self.assertIsNone(result.payload["snapshot"])
            self.assertEqual(len(local.calls), 1)

    def test_malformed_missing_nonnumeric_and_invalid_book(self):
        for changes in ({"Now": None}, {"Volume": "lots"}, {"Amount": -1}, {"Open": True},
                        {"Buyp": [1]}, {"Buyv": [1, 2, 3, 4, "5"]}):
            result, _ = fetch_result(transport=FakeLocal(changes))
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertIsNone(result.payload["snapshot"])
        for raw in (b"broken", b'{"error":{"code":-32700}}'):
            result, _ = fetch_result(transport=FakeLocal(raw=raw))
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
        for value in (float("nan"), float("inf"), 10 ** 500):
            data = json.dumps({"result": {"ErrorId": "0", **SNAPSHOT, "Now": value}}).encode()
            result, _ = fetch_result(transport=FakeLocal(raw=data))
            self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)
            self.assertIsNone(result.payload["snapshot"])

    def test_response_identity_mismatch_fails(self):
        result, _ = fetch_result(transport=FakeLocal(raw=json_bytes({"id": "wrong", "result": {"ErrorId": "0", **SNAPSHOT}})))
        self.assertEqual(result.status, ProviderStatus.VALIDATION_FAILED)

    def test_invalid_scope_stock_id_and_path_do_not_call_transport(self):
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={})
        for request in ({"data_kind": "historical_daily"}, {"data_kind": "realtime_quote", "stock_id": "wrong"},
                        {"data_kind": "realtime_quote", "stock_id": "601518.SH", "request_id": "../escape"}):
            self.assertEqual(provider.fetch(request).status, ProviderStatus.VALIDATION_FAILED)
        self.assertEqual(local.calls, [])

    def test_cloud_runtime_never_connects(self):
        with patch("providers.tdx_realtime.sys.platform", "linux"), patch.object(LocalHTTPTransport, "post") as post:
            provider = TdxLocalHttpProvider(environ={})
            self.assertEqual(provider.health_check(), ProviderStatus.UNKNOWN)
            self.assertEqual(provider.fetch("601518.SH").payload["realtime_quote_status"], "MISSING")
            post.assert_not_called()

    def test_health_probe_does_not_fetch_snapshot(self):
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={})
        self.assertEqual(provider.health_check(), ProviderStatus.AVAILABLE)
        self.assertEqual(local.calls, [])
        self.assertEqual(local.health_calls, [{"timeout": 5}])

    def test_endpoint_rejects_remote_credentials_and_redirect_targets(self):
        for url in ("http://example.com/", "https://127.0.0.1/", "http://user@127.0.0.1/",
                    "http://127.0.0.1/?token=x", "http://127.0.0.1/other"):
            with self.assertRaises(ValueError):
                LocalHTTPTransport(url)

    def test_http_post_header_and_split_deadlines_mocked(self):
        with patch("providers.tdx_realtime.http.client.HTTPConnection") as connection_class:
            connection = connection_class.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = b"{}"
            self.assertEqual(LocalHTTPTransport("http://127.0.0.1:17709/").post(b"{}", connect_timeout=3, read_timeout=10), b"{}")
            connection_class.assert_called_once_with("127.0.0.1", 17709, timeout=3)
            connection.sock.settimeout.assert_called_once_with(10)
            connection.request.assert_called_once_with("POST", "/", body=b"{}", headers={"Content-Type": "application/json; charset=utf-8"})
            connection.close.assert_called_once()


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.store = IngressStore(self.root / "cloud", environ=TEST_ENV, clock=self.clock)

    def accept(self, value):
        return self.store.receive("Bearer TEST_TOKEN_ONLY", value)

    def receipts(self):
        return list((self.root / "cloud" / "receipts").glob("*.json"))

    def test_required_envelope_fields_and_modes(self):
        value = packet()
        self.assertEqual(set(value), FIELDS)
        for field in ("stock_id", "source", "runtime_node", "request_timestamp", "provider_timestamp",
                      "snapshot_timestamp", "raw_response_hash", "request_id", "system_run_id",
                      "collection_task_id", "freshness", "collection_mode"):
            self.assertIn(field, value)
        self.assertEqual(value["collection_mode"], "AUTO")
        self.assertEqual(packet(mode="MANUAL")["collection_mode"], "MANUAL")

    def test_auth_missing_wrong_token_and_no_persistence(self):
        for auth in (None, "", "Bearer WRONG_TEST_TOKEN_ONLY"):
            with self.assertRaises(IngressError) as error:
                self.store.receive(auth, packet())
            self.assertEqual(error.exception.status, 401)
        self.assertEqual(self.receipts(), [])

    def test_duplicate_exact_ack_and_one_effect(self):
        value = packet()
        ack = self.accept(value)
        self.clock.now += timedelta(seconds=400)
        self.assertEqual(self.accept(value), ack)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(ack["freshness"], "REALTIME")

    def test_same_key_different_contents_reports_conflict(self):
        self.accept(packet())
        changed = packet(transport=FakeLocal({"Now": 4.25}))
        with self.assertRaises(IngressError) as error:
            self.accept(changed)
        self.assertEqual((error.exception.status, error.exception.code), (409, "IDEMPOTENCY_CONFLICT"))
        self.assertEqual(len(self.receipts()), 1)

    def test_same_key_changed_mode_is_conflict_not_duplicate(self):
        self.accept(packet())
        with self.assertRaises(IngressError) as error:
            self.accept(packet(mode="MANUAL"))
        self.assertEqual(error.exception.status, 409)

    def test_raw_byte_evidence_hash_and_normalization_rules(self):
        units = {"price": "yuan", "volume": "lot", "amount": "万元"}
        value = packet(units=units)
        self.accept(value)
        receipt = json.loads(self.receipts()[0].read_bytes())
        evidence = receipt["result"]["evidence"]
        data = Path(evidence["raw_response_path"]).read_bytes()
        self.assertEqual(data, base64.b64decode(value["raw_response_b64"]))
        self.assertEqual(hashlib.sha256(data).hexdigest(), evidence["raw_response_hash"])
        self.assertEqual(evidence["raw_response_hash"], value["raw_response_hash"])
        self.assertIn("volume:LOT100_TO_SHARE", evidence["normalization_rule"])
        self.assertIn("amount:TEN_THOUSAND_YUAN_TO_YUAN", evidence["normalization_rule"])
        canonical = receipt["result"]["canonical_data"][0]
        self.assertEqual(canonical["volume"], SNAPSHOT["Volume"] * 100)
        self.assertEqual(canonical["amount"], SNAPSHOT["Amount"] * 10000)
        self.assertIsNone(canonical["quote_timestamp"])
        self.assertEqual(canonical["freshness"], "UNKNOWN")
        self.assertFalse(self.store.current("601518.SH")["usable_for_realtime"])
        self.assertNotIn("TEST_TOKEN_ONLY", "".join(p.read_text() for p in self.root.rglob("*.json")))

    def test_units_missing_not_guessed_and_wrong_units_rejected(self):
        self.accept(packet())
        receipt = json.loads(self.receipts()[0].read_bytes())
        self.assertEqual(receipt["result"]["canonical_data"], [])
        self.assertEqual(receipt["result"]["evidence"]["validation_result"]["status"], "FAIL")
        self.assertIsNone(receipt["result"]["normalization"]["normalized_data"][0]["numeric_fields"]["price"]["normalized_value"])
        value = packet(key="bad_units")
        value["units"] = {"price": "cents"}
        with self.assertRaises(IngressError) as error:
            self.accept(value)
        self.assertEqual(error.exception.status, 422)

    def test_required_field_missing_preserves_raw_evidence_without_canonical(self):
        value = packet(transport=FakeLocal({"Now": None}))
        self.assertEqual(self.accept(value)["freshness"], "MISSING")
        receipt = json.loads(self.receipts()[0].read_bytes())
        self.assertEqual(receipt["result"]["canonical_data"], [])
        self.assertEqual(json.loads(Path(receipt["result"]["evidence"]["raw_response_path"]).read_bytes())["result"]["Now"], None)

    def test_rpc_identity_failure_remains_missing_with_raw_evidence(self):
        data = json_bytes({"id": "wrong", "result": {"ErrorId": "0", **SNAPSHOT}})
        value = packet(transport=FakeLocal(raw=data))
        self.assertEqual(self.accept(value)["freshness"], "MISSING")
        receipt = json.loads(self.receipts()[0].read_bytes())
        self.assertEqual(receipt["result"]["canonical_data"], [])
        self.assertEqual(Path(receipt["result"]["evidence"]["raw_response_path"]).read_bytes(), data)

    def test_freshness_boundary_and_missing(self):
        at = NOW.isoformat()
        self.assertEqual(freshness(at, now=NOW, stale_after=300), "REALTIME")
        self.assertEqual(freshness(at, now=NOW + timedelta(seconds=300), stale_after=300), "REALTIME")
        self.assertEqual(freshness(at, now=NOW + timedelta(seconds=301), stale_after=300), "STALE")
        self.assertEqual(freshness(None, now=NOW, stale_after=300), "MISSING")

    def test_stale_snapshot_blocks_current_realtime_data(self):
        value = packet(units={"price": "yuan", "volume": "share", "amount": "yuan"})
        self.clock.now += timedelta(seconds=301)
        self.assertEqual(self.accept(value)["freshness"], "STALE")
        current = self.store.current("601518.SH")
        self.assertEqual(current["realtime_quote_status"], "STALE")
        self.assertEqual(current["canonical_data"], [])
        self.assertFalse(current["usable_for_realtime"])

    def test_old_arrival_keeps_new_quote_and_both_evidence(self):
        old = packet(key="old")
        self.clock.now += timedelta(seconds=400)
        new = packet(key="new", clock=self.clock, transport=FakeLocal({"Now": 4.25}))
        self.accept(new)
        self.assertEqual(self.accept(old)["freshness"], "STALE")
        self.assertEqual(self.store.current("601518.SH")["request_id"], "new")
        self.assertEqual(len(self.receipts()), 2)
        for path in self.receipts():
            evidence = json.loads(path.read_bytes())["result"]["evidence"]
            self.assertTrue(Path(evidence["raw_response_path"]).is_file())

    def test_tdx_exit_masks_previous_snapshot_and_survives_late_replay(self):
        good = packet()
        self.accept(good)
        self.clock.now += timedelta(seconds=60)
        missing = packet(key="missing", clock=self.clock, transport=FakeLocal(failure=ConnectionRefusedError()))
        self.assertEqual(self.accept(missing)["freshness"], "MISSING")
        self.accept(good)
        current = self.store.current("601518.SH")
        self.assertEqual(current["realtime_quote_status"], "MISSING")
        self.assertEqual(current["canonical_data"], [])
        self.assertFalse(current["usable_for_realtime"])

    def test_pc_off_no_contact_becomes_missing_without_cache_fallback(self):
        store = IngressStore(self.root / "silent", environ={"TDX_INGRESS_TOKEN": "TEST_TOKEN_ONLY"}, clock=self.clock)
        self.assertEqual(store.current("601518.SH")["realtime_quote_status"], "MISSING")
        value = packet()
        store.receive("Bearer TEST_TOKEN_ONLY", value)
        self.clock.now += timedelta(seconds=121)
        store.receive("Bearer TEST_TOKEN_ONLY", value)  # replay cannot refresh heartbeat
        current = store.current("601518.SH")
        self.assertEqual(current["realtime_quote_status"], "MISSING")
        self.assertEqual(current["canonical_data"], [])

    def test_tampered_snapshot_raw_hash_identity_future_and_extra_fields_rejected(self):
        variants = []
        for field, value in (("source", "CLOUD_RECEIVER"), ("runtime_node", "AWS"), ("collection_mode", "OTHER"),
                             ("snapshot_timestamp", (NOW + timedelta(seconds=1)).isoformat()),
                             ("raw_response_hash", "0" * 64), ("provider_timestamp", NOW.isoformat()),
                             ("request_id", "../bad")):
            changed = packet()
            changed[field] = value
            variants.append(changed)
        changed = packet()
        changed["snapshot"]["now"] = 999
        variants.append(changed)
        changed = packet()
        changed["unexpected"] = "value"
        variants.append(changed)
        changed = packet()
        del changed["snapshot_timestamp"]
        variants.append(changed)
        for value in variants:
            with self.assertRaises(IngressError) as error:
                self.accept(value)
            self.assertEqual(error.exception.status, 422)
        self.assertEqual(self.receipts(), [])

    def test_sensitive_material_rejected_before_evidence(self):
        for change in ({"ErrorInfo": "TEST_TOKEN_ONLY"}, {"api_key": "placeholder"}):
            value = packet()
            raw = json.loads(base64.b64decode(value["raw_response_b64"]))
            raw["result"].update(change)
            data = json_bytes(raw)
            value["raw_response_b64"] = base64.b64encode(data).decode()
            value["raw_response_hash"] = hashlib.sha256(data).hexdigest()
            with self.assertRaises(IngressError):
                self.accept(value)
        self.assertEqual(self.receipts(), [])
        self.assertFalse((self.root / "cloud" / "evidence").exists())

    def test_ack_loss_and_process_restart_drain_exact_original_once(self):
        value = packet()
        local_root = self.root / "windows"
        box = Outbox(local_root, clock=self.clock)
        box.enqueue(value)
        sender = DirectSender(self.store, lose_ack=True)
        self.assertFalse(box.flush(sender))
        self.assertEqual(box.count(), 1)
        original = next(box.pending.glob("*.json")).read_bytes()
        del box
        store = IngressStore(self.root / "cloud", environ=TEST_ENV, clock=self.clock)
        restarted = Outbox(local_root, clock=self.clock)
        new_sender = DirectSender(store)
        self.assertTrue(restarted.flush(new_sender))
        self.assertEqual(restarted.count(), 0)
        self.assertEqual(json_bytes(new_sender.calls[0]), original)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(sender.calls[0], new_sender.calls[0])

    def test_storage_failure_no_ack_then_retry_commits_once(self):
        value = packet()
        from bridge.pipeline import atomic_write as real_write

        def fail_receipt(path, *args, **kwargs):
            if path.parent.name == "receipts":
                raise OSError("disk failure")
            return real_write(path, *args, **kwargs)

        with patch("bridge.ingress.atomic_write", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.accept(value)
        self.assertEqual(self.receipts(), [])
        self.assertTrue(self.accept(value)["stored"])
        self.assertEqual(len(self.receipts()), 1)

    def test_disconnect_backlog_bounded_visible_and_no_drop(self):
        box = Outbox(self.root / "windows", clock=self.clock)
        for index in range(5):
            box.enqueue(packet(key=f"backlog_{index}"))
        sender = DirectSender(self.store, fail=True)
        self.assertFalse(box.flush(sender, batch_size=2))
        self.assertEqual(box.count(), 5)
        self.assertEqual(len(sender.calls), 1)
        sender.fail = False
        self.assertFalse(box.flush(sender, batch_size=2))
        self.assertEqual(box.count(), 3)
        self.assertFalse(box.flush(sender, batch_size=2))
        self.assertEqual(box.count(), 1)
        self.assertTrue(box.flush(sender, batch_size=2))
        self.assertEqual(len(self.receipts()), 5)
        log = (box.root / "collector.log").read_text()
        self.assertIn('"pending_count":3', log)
        self.assertNotIn("TEST_TOKEN_ONLY", log)

    def test_invalid_ack_never_deletes_envelope(self):
        box = Outbox(self.root / "windows", clock=self.clock)
        box.enqueue(packet())
        sender = MagicMock()
        sender.send.return_value = {"stored": True, "request_id": "request_1", "packet_hash": "wrong"}
        self.assertFalse(box.flush(sender))
        self.assertEqual(box.count(), 1)

    def test_cloud_conflict_preserves_pending(self):
        self.accept(packet())
        box = Outbox(self.root / "windows", clock=self.clock)
        box.enqueue(packet(transport=FakeLocal({"Now": 4.25})))
        sender = DirectSender(self.store)
        # Direct mock uses the same HTTP-facing status failure that urllib does.
        with patch.object(sender, "send", side_effect=HTTPError("https://example.invalid/realtime", 409, "conflict", {}, None)):
            self.assertFalse(box.flush(sender))
        self.assertEqual(box.count(), 1)
        self.assertIn("IDEMPOTENCY_CONFLICT", (box.root / "collector.log").read_text())

    def test_outbox_capacity_pauses_collection_but_recovers_backlog(self):
        root = self.root / "windows"
        box = Outbox(root, clock=self.clock)
        box.enqueue(packet())
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={}, clock=self.clock)
        result = run_once("601518.SH", state_dir=root,
                          environ={**TEST_ENV, "TDX_OUTBOX_MAX_BYTES": str(MAX_PACKET_BYTES)},
                          provider=provider, sender=DirectSender(self.store), clock=self.clock)
        self.assertEqual(result, 2)
        self.assertEqual(local.calls, [])
        self.assertEqual(box.count(), 0)
        self.assertEqual(len(self.receipts()), 1)
        self.assertIn("OUTBOX_CAPACITY_COLLECTION_PAUSED", (root / "collector.log").read_text())

    def test_run_exit_codes_missing_and_unreachable(self):
        for name, failure, cloud_fail, expected in (("ok", None, False, 0),
                                                   ("tdx", ConnectionRefusedError(), False, 1),
                                                   ("cloud", None, True, 2),
                                                   ("both", ConnectionRefusedError(), True, 1)):
            provider = TdxLocalHttpProvider(FakeLocal(failure=failure), environ={}, clock=self.clock)
            sender = DirectSender(self.store, fail=cloud_fail)
            root = self.root / name
            self.assertEqual(run_once("601518.SH", state_dir=root, environ=TEST_ENV, provider=provider,
                                     sender=sender, clock=self.clock), expected)
            if cloud_fail:
                self.assertEqual(Outbox(root, clock=self.clock).count(), 1)
            self.assertNotIn("TEST_TOKEN_ONLY", (root / "collector.log").read_text())

    def test_invalid_push_configuration_still_retains_raw_evidence_and_envelope(self):
        root = self.root / "windows"
        provider = TdxLocalHttpProvider(FakeLocal(), environ={}, clock=self.clock)
        self.assertEqual(run_once("601518.SH", state_dir=root, environ={}, provider=provider, clock=self.clock), 2)
        self.assertEqual(Outbox(root, clock=self.clock).count(), 1)
        self.assertEqual(len(list((root / "evidence").rglob("raw.json"))), 1)
        self.assertEqual(len(list((root / "evidence").rglob("evidence.json"))), 1)

    def test_throttle_minimum_and_closed_session_do_not_poll(self):
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={}, clock=self.clock)
        sender = DirectSender(self.store)
        root = self.root / "windows"
        for _ in range(2):
            self.assertEqual(run_once("601518.SH", state_dir=root, environ=TEST_ENV,
                                     provider=provider, sender=sender, clock=self.clock), 0)
        self.assertEqual(len(local.calls), 1)
        self.clock.now += timedelta(seconds=60)
        run_once("601518.SH", state_dir=root, environ=TEST_ENV, provider=provider, sender=sender, clock=self.clock)
        self.assertEqual(len(local.calls), 2)
        with self.assertRaises(ValueError):
            run_once("601518.SH", state_dir=root, environ={**TEST_ENV, "REALTIME_POLL_SECONDS": "29"})
        closed = Clock(NOW.replace(hour=10))
        run_once("601518.SH", state_dir=self.root / "closed", environ={**TEST_ENV, "TDX_SKIP_CLOSED_SESSIONS": "1"},
                 provider=provider, sender=sender, clock=closed)
        self.assertEqual(len(local.calls), 2)

    def test_manual_mode_and_push_only_recovery(self):
        local = FakeLocal()
        provider = TdxLocalHttpProvider(local, environ={}, clock=self.clock)
        sender = DirectSender(self.store, fail=True)
        root = self.root / "windows"
        self.assertEqual(run_once("601518.SH", state_dir=root, mode="MANUAL", environ=TEST_ENV,
                                 provider=provider, sender=sender, clock=self.clock), 2)
        pending = json.loads(next((root / "pending").glob("*.json")).read_bytes())
        self.assertEqual(pending["collection_mode"], "MANUAL")
        sender.fail = False
        self.assertEqual(run_once("601518.SH", state_dir=root, environ=TEST_ENV, provider=provider,
                                 sender=sender, clock=self.clock, push_only=True), 0)
        self.assertEqual(len(local.calls), 1)

    def test_process_lock_prevents_overlapping_collectors_and_releases(self):
        lock = self.root / "lock"
        with ProcessLock(lock):
            with self.assertRaises(OSError):
                with ProcessLock(lock):
                    pass
        with ProcessLock(lock):
            pass

    def test_windows_lock_branch_uses_standard_library_byte_lock(self):
        locking = MagicMock()
        module = SimpleNamespace(locking=locking, LK_NBLCK=2)
        lock = ProcessLock(self.root / "windows.lock")
        with patch.dict("sys.modules", {"msvcrt": module}), patch("bridge.pipeline.os.name", "nt"):
            with lock:
                self.assertIsNotNone(lock.handle)
                locking.assert_called_once_with(lock.handle.fileno(), 2, 1)
        self.assertTrue(lock.handle.closed)

    def test_offline_mocked_full_wire_path(self):
        # Serialize Windows packet -> mocked urllib -> real HTTP handler logic
        # -> frozen pipeline -> serialize ack. No real TCP is used.
        box = Outbox(self.root / "windows", clock=self.clock)
        value = packet()
        box.enqueue(value)
        sender = PushHTTPTransport("https://example.invalid/realtime", "TEST_TOKEN_ONLY")

        def invoke(outgoing, **kwargs):
            status, body = self.http_call(outgoing.data, outgoing.get_header("Authorization"))
            self.assertEqual(status, 200)
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = status
            response.read.return_value = body
            return response

        with patch.object(sender._opener, "open", side_effect=invoke) as opening:
            self.assertTrue(box.flush(sender, credential="TEST_TOKEN_ONLY"))
            self.assertEqual(opening.call_count, 1)
        self.assertEqual(len(self.receipts()), 1)
        self.assertEqual(box.count(), 0)

    def http_call(self, body, auth="Bearer TEST_TOKEN_ONLY", *, size=None, path="/realtime"):
        handler = handler_for(self.store).__new__(handler_for(self.store))
        handler.path = path
        handler.headers = Message()
        if auth is not None:
            handler.headers["Authorization"] = auth
        handler.headers["Content-Length"] = str(len(body) if size is None else size)
        handler.headers["Content-Type"] = "application/json"
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.connection = MagicMock()
        statuses = []
        handler.send_response = statuses.append
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return statuses[0], handler.wfile.getvalue()

    def test_http_receiver_auth_parse_limits_and_fixed_error_bodies(self):
        for auth in (None, "Bearer WRONG_TEST_TOKEN_ONLY"):
            status, body = self.http_call(b"not even json", auth)
            self.assertEqual(status, 401)
            self.assertNotIn(b"TEST_TOKEN_ONLY", body)
        self.assertEqual(self.http_call(b"broken")[0], 422)
        self.assertEqual(self.http_call(b"{}", size=MAX_PACKET_BYTES + 1)[0], 413)
        self.assertEqual(self.http_call(b"{}", size=20)[0], 400)
        self.assertEqual(self.http_call(b"{}", path="/wrong")[0], 404)


class StructureTests(unittest.TestCase):
    def test_receiver_has_no_network_client_provider_or_control_path(self):
        path = Path(__file__).resolve().parents[1] / "bridge" / "ingress.py"
        tree = ast.parse(path.read_text())
        imported = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)
        self.assertFalse(imported & {"urllib.request", "http.client", "providers.tdx_realtime", "subprocess"})
        self.assertNotIn("TdxLocalHttpProvider", calls)
        self.assertNotIn("get_market_snapshot", path.read_text())

    def test_provider_rpc_method_allowlist_is_snapshot_only(self):
        path = Path(__file__).resolve().parents[1] / "providers" / "tdx_realtime.py"
        tree = ast.parse(path.read_text())
        methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "method":
                        self.assertIsInstance(value, ast.Constant)
                        methods.append(value.value)
        self.assertEqual(methods, ["get_market_snapshot"])

    def test_https_auth_transport_no_redirect_or_url_secrets(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, None, None, None, None))
        for url in ("http://example.invalid/realtime", "https://user@example.invalid/", "https://example.invalid/?token=x"):
            with self.assertRaises(ValueError):
                PushHTTPTransport(url, "TEST_TOKEN_ONLY")
        with self.assertRaises(ValueError):
            PushHTTPTransport("https://example.invalid/realtime", "")

    def test_session_uses_china_clock_and_does_not_guess_holidays(self):
        self.assertTrue(market_session(NOW, skip_closed=False))
        self.assertFalse(market_session(NOW, skip_closed=True))  # 11:30 CST lunch
        self.assertTrue(market_session(NOW - timedelta(minutes=1), skip_closed=True))
        self.assertFalse(market_session(NOW + timedelta(days=1), skip_closed=True))


if __name__ == "__main__":
    unittest.main()
