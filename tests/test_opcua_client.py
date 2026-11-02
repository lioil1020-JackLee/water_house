"""Offline tests using the real worker with fake OPC UA services."""

import asyncio
import os
import threading
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from asyncua import ua
from asyncua.common.subscription import Subscription, SubscriptionItemData
from PyQt6.QtWidgets import QApplication

from ui.opcua_client import OPCUAClient, _SubscriptionHandler


def data_value(value, status=ua.StatusCodes.Good):
    return ua.DataValue(ua.Variant(value), ua.StatusCode(status))


class FakeNode:
    def __init__(self, node_id):
        self.nodeid = node_id
        self.writes = []
        self.fail_write = False

    async def write_value(self, value):
        if self.fail_write:
            raise ConnectionError("synthetic write failure")
        self.writes.append(value)


class FakeSubscription:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.rejected = set()
        self.deleted = False

    async def subscribe_data_change(self, nodes, **kwargs):
        self.requests.append((list(nodes), kwargs))
        return [
            (
                ua.StatusCode(ua.StatusCodes.BadNodeIdUnknown)
                if node.nodeid in self.rejected
                else index + 1
            )
            for index, node in enumerate(nodes)
        ]

    async def delete(self):
        self.deleted = True


class FakeClient:
    def __init__(self, url="fake"):
        self.nodes = {}
        self.values = {}
        self.read_batches = []
        self.handler = None
        self.subscription = None
        self.fail_connect = False
        self.fail_batch_nodes = set()
        self.on_read = None
        self.disconnected = False

    async def connect(self):
        if self.fail_connect:
            raise ConnectionError("synthetic connection failure")

    async def disconnect(self):
        self.disconnected = True

    async def check_connection(self):
        return None

    def get_node(self, node_id):
        if node_id not in self.nodes:
            self.nodes[node_id] = FakeNode(node_id)
        return self.nodes[node_id]

    async def create_subscription(self, period, handler):
        self.handler = handler
        self.subscription = FakeSubscription(handler)
        return self.subscription

    async def read_attributes(self, nodes):
        self.read_batches.append([node.nodeid for node in nodes])
        if self.on_read:
            await self.on_read(nodes)
        if any(node.nodeid in self.fail_batch_nodes for node in nodes):
            raise ConnectionError("synthetic failed chunk")
        return [self.values.get(node.nodeid, data_value(False)) for node in nodes]


class OPCUAClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.worker = OPCUAClient("fake-no-network", [("A", "node-A"), ("B", "node-B")])
        self.worker._worker_alive = True
        self.fake = FakeClient()
        self.worker.client = self.fake
        self.worker._transport_connected = True
        self.worker._subscription_ready = True
        self.worker.nodes = {
            tag: self.fake.get_node(node) for tag, node in self.worker.tag_list
        }
        self.worker._node_to_tag = {
            node.nodeid: tag for tag, node in self.worker.nodes.items()
        }
        self.updates = []
        self.health = []
        self.lost = []
        self.restored = []
        self.write_failed = []
        self.worker.update_signal.connect(self.updates.append)
        self.worker.health_signal.connect(self.health.append)
        self.worker.connection_lost_signal.connect(lambda: self.lost.append(True))
        self.worker.connection_restored_signal.connect(
            lambda: self.restored.append(True)
        )
        self.worker.write_failed_signal.connect(self.write_failed.append)

    def apply(self, tag, value, status=ua.StatusCodes.Good):
        return self.worker._apply_data_value(
            tag, data_value(value, status), origin="test"
        )

    def notification(self, handler, tag, value, status=ua.StatusCodes.Good):
        handler.datachange_notification(
            self.worker.nodes[tag],
            value,
            SimpleNamespace(
                monitored_item=SimpleNamespace(Value=data_value(value, status))
            ),
        )

    def test_subscription_keeps_true_false_true_in_one_publish(self):
        handler = _SubscriptionHandler(self.worker)
        self.notification(handler, "A", False)
        self.notification(handler, "A", True)
        self.notification(handler, "A", False)
        self.notification(handler, "A", True)
        self.assertEqual(
            self.updates, [{"A": False}, {"A": True}, {"A": False}, {"A": True}]
        )

    def test_asyncua_118_publish_dispatch_preserves_queued_transitions(self):
        handler = _SubscriptionHandler(self.worker)
        subscription = Subscription(None, ua.CreateSubscriptionParameters(), handler)
        item = SubscriptionItemData()
        item.node = self.worker.nodes["A"]
        subscription._monitored_items[1] = item
        notification = ua.DataChangeNotification()
        notification.MonitoredItems = [
            ua.MonitoredItemNotification(ClientHandle=1, Value=data_value(value))
            for value in (False, True, False, True)
        ]
        asyncio.run(subscription._call_datachange(notification))
        self.assertEqual(
            self.updates, [{"A": False}, {"A": True}, {"A": False}, {"A": True}]
        )

    def test_cache_read_has_no_network_or_blocking_future(self):
        self.apply("A", True)
        self.assertIs(self.worker.read_value("A"), True)
        self.assertIsNone(self.worker.read_value("missing"))
        self.assertEqual(self.fake.read_batches, [])

    def test_bad_quality_is_not_a_valid_alarm_value(self):
        self.apply("A", False)
        self.apply("B", False)
        self.apply("A", True, ua.StatusCodes.BadNoCommunication)
        self.assertNotIn({"A": True}, self.updates)
        self.assertIsNone(self.worker.read_value("A"))
        self.assertEqual(self.worker.tag_health["A"]["quality"], "bad")
        self.assertFalse(self.worker.health_snapshot()["monitoring_healthy"])
        self.assertEqual(
            len(self.lost), 2
        )  # initially incomplete, then bad after recovery

    def test_one_good_tag_does_not_mask_another_bad_tag(self):
        self.apply("A", True)
        self.apply("B", False, ua.StatusCodes.BadNodeIdUnknown)
        self.assertFalse(self.worker.is_connected)
        self.assertEqual(self.worker.health_snapshot()["bad_tags"], ["B"])
        self.assertEqual(self.restored, [])

    def test_health_snapshot_is_a_copy_and_expires_unchanged_points(self):
        self.apply("A", False)
        self.apply("B", False)
        snapshot = self.worker.health_snapshot()
        snapshot["tag_health"]["A"]["quality"] = "bad"
        self.assertEqual(self.worker.tag_health["A"]["quality"], "good")
        self.worker._good_monotonic["A"] = time.monotonic() - 100
        self.assertEqual(self.worker.health_snapshot()["stale_tags"], ["A"])
        self.assertIsNone(self.worker.read_value("A"))
        self.assertFalse(self.worker.health_snapshot()["monitoring_healthy"])

    def test_unchanged_compensation_read_refreshes_freshness(self):
        self.apply("A", False)
        self.apply("B", False)
        old = self.worker.health_snapshot()["last_data_at"]
        self.worker._good_monotonic["A"] = time.monotonic() - 100
        asyncio.run(self.worker._read_snapshot())
        self.assertGreaterEqual(self.worker.health_snapshot()["last_data_at"], old)
        self.assertEqual(self.worker.health_snapshot()["stale_tags"], [])

    def test_batch_read_148_tags_uses_five_requests(self):
        self.worker = OPCUAClient("fake", [(f"T{i}", f"N{i}") for i in range(148)])
        self.worker.client = self.fake
        self.worker._transport_connected = True
        self.worker._subscription_ready = True
        self.worker.nodes = {
            tag: self.fake.get_node(node) for tag, node in self.worker.tag_list
        }
        self.assertEqual(asyncio.run(self.worker._read_snapshot()), 148)
        self.assertEqual(
            [len(chunk) for chunk in self.fake.read_batches], [32, 32, 32, 32, 20]
        )

    def test_failed_chunk_does_not_hide_other_chunk_results(self):
        self.worker.chunk_size = 1
        self.fake.fail_batch_nodes = {"node-B"}
        asyncio.run(self.worker._read_snapshot())
        self.assertIs(self.worker.read_value("A"), False)
        self.assertIsNone(self.worker.read_value("B"))
        self.assertEqual(self.worker.tag_health["B"]["quality"], "bad")

    def test_initial_snapshot_cannot_overwrite_newer_subscription_value(self):
        handler = _SubscriptionHandler(self.worker)

        async def arriving_during_read(nodes):
            self.notification(handler, "A", True)

        self.fake.on_read = arriving_during_read
        asyncio.run(self.worker._read_snapshot())
        self.assertIs(self.worker.read_value("A"), True)
        self.assertNotIn({"A": False}, self.updates)

    def test_snapshot_failure_cannot_invalidate_newer_notification(self):
        handler = _SubscriptionHandler(self.worker)
        self.fake.fail_batch_nodes = {"node-A"}

        async def arriving_during_read(nodes):
            self.notification(handler, "A", True)

        self.fake.on_read = arriving_during_read
        asyncio.run(self.worker._read_snapshot())
        self.assertIs(self.worker.read_value("A"), True)
        self.assertEqual(self.worker.tag_health["A"]["quality"], "good")

    def test_subscription_requests_fifo_queue_and_fast_sampling(self):
        asyncio.run(self.worker._create_subscription())
        options = self.fake.subscription.requests[0][1]
        self.assertEqual(options["queuesize"], 128)
        self.assertEqual(options["sampling_interval"], 100.0)

    def test_rejected_monitored_item_does_not_report_healthy(self):
        original = self.fake.create_subscription

        async def rejecting(period, handler):
            subscription = await original(period, handler)
            subscription.rejected = {"node-B"}
            return subscription

        self.fake.create_subscription = rejecting
        self.worker._subscription_ready = False
        with self.assertRaisesRegex(RuntimeError, "1 個"):
            asyncio.run(self.worker._create_subscription())
        self.assertFalse(self.worker.health_snapshot()["monitoring_healthy"])
        self.assertEqual(self.worker.tag_health["B"]["quality"], "bad")

    def test_subscription_created_before_snapshot(self):
        sequence = []

        async def subscribe():
            sequence.append("subscribe")

        async def snapshot():
            sequence.append("snapshot")
            self.worker.running = False
            return 2

        self.worker._create_subscription = subscribe
        self.worker._read_snapshot = snapshot
        asyncio.run(self.worker._monitor_connection())
        self.assertEqual(sequence, ["subscribe", "snapshot"])

    def test_successful_initial_snapshot_does_not_create_false_connection_alarm(self):
        original_create = self.fake.create_subscription

        async def create(period, handler):
            subscription = await original_create(period, handler)
            self.notification(handler, "A", False)
            return subscription

        self.fake.create_subscription = create
        original_read = self.worker._read_snapshot

        async def stop_after_snapshot():
            count = await original_read()
            self.worker.running = False
            return count

        self.worker._read_snapshot = stop_after_snapshot
        asyncio.run(self.worker._monitor_connection())
        self.assertEqual(self.lost, [])
        self.assertEqual(len(self.restored), 1)

    def test_network_failure_recovers_without_exiting_worker(self):
        attempts = []

        def factory(**kwargs):
            fake = FakeClient()
            fake.fail_connect = not attempts
            attempts.append(fake)
            return fake

        self.worker._client_factory = factory
        original_read = self.worker._read_snapshot

        async def stop_after_snapshot():
            count = await original_read()
            self.worker.running = False
            return count

        self.worker._read_snapshot = stop_after_snapshot
        original_close = self.worker._close_connection

        async def wake_retry():
            await original_close()
            if len(attempts) == 1:
                asyncio.get_running_loop().call_soon(self.worker._wake_event.set)

        self.worker._close_connection = wake_retry
        asyncio.run(self.worker._run_client())
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(self.lost), 1)
        self.assertEqual(len(self.restored), 1)
        self.assertEqual(self.worker.read_value("A"), False)

    def test_old_session_callbacks_are_ignored_after_disconnect(self):
        handler = _SubscriptionHandler(self.worker)
        self.worker._mark_disconnected("test")
        self.worker._transport_connected = True
        self.notification(handler, "A", True)
        self.assertEqual(self.updates, [])

    def test_initial_connection_failure_emits_lost_and_retries_inside_worker(self):
        attempts = []

        def factory(**kwargs):
            fake = FakeClient()
            fake.fail_connect = True
            attempts.append(fake)
            return fake

        self.worker._client_factory = factory

        async def close():
            self.worker.running = False

        self.worker._close_connection = close
        asyncio.run(self.worker._run_client())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(self.lost), 1)
        self.assertFalse(self.worker.health_snapshot()["connected"])

    def test_worker_fatal_failure_invalidates_cached_data(self):
        class Fatal(BaseException):
            pass

        self.apply("A", True)

        async def fail():
            raise Fatal("synthetic worker death")

        self.worker._run_client = fail
        self.worker.run()
        self.assertFalse(self.worker.health_snapshot()["worker_alive"])
        self.assertIsNone(self.worker.read_value("A"))
        self.assertFalse(self.worker.health_snapshot()["monitoring_healthy"])

    def test_write_not_connected_signals_failure_and_clears_pending(self):
        self.worker.current_values["A"] = True
        self.worker.write_value("A", True)
        self.assertEqual(self.write_failed, ["A"])
        self.assertNotIn("A", self.worker.current_values)

    def test_async_write_preserves_boolean_type_and_reports_failure(self):
        asyncio.run(self.worker._write_async("A", True))
        variant = self.worker.nodes["A"].writes[0].Value
        self.assertIs(variant.Value, True)
        self.assertEqual(variant.VariantType, ua.VariantType.Boolean)
        self.worker.nodes["A"].fail_write = True
        asyncio.run(self.worker._write_async("A", False))
        self.assertEqual(self.write_failed, ["A"])

    def test_server_alarm_is_never_masked_by_pending_optimistic_write(self):
        self.worker.current_values["A"] = False
        self.worker.write_timestamps["A"] = time.time()
        self.apply("A", True)
        self.assertEqual(self.updates, [{"A": True}])
        self.assertIs(self.worker.read_value("A"), True)

    def test_subscription_overflow_marks_monitoring_unhealthy(self):
        self.apply("A", True, ua.StatusCodes.Good | 0x480)
        self.assertFalse(self.worker.health_snapshot()["monitoring_healthy"])
        self.assertIn("溢位", self.worker._subscription_error)

    def test_disconnect_fails_unconfirmed_writes(self):
        self.worker.current_values["A"] = True
        self.worker.write_timestamps["A"] = time.time()
        self.worker._mark_disconnected("test")
        self.assertEqual(self.worker.current_values, {})
        self.assertEqual(self.write_failed, ["A"])

    def test_qthread_stop_cancels_stalled_connect_without_blocking_gui(self):
        entered = threading.Event()

        class StalledClient(FakeClient):
            async def connect(self):
                entered.set()
                await asyncio.Event().wait()

        worker = OPCUAClient(
            "fake-no-network", [("A", "node-A")], client_factory=StalledClient
        )
        worker.start()
        try:
            self.assertTrue(entered.wait(timeout=2.0))
            before = time.monotonic()
            worker.stop()
            self.assertLess(time.monotonic() - before, 0.2)
            self.assertTrue(worker.wait(2000))
            self.assertFalse(worker.health_snapshot()["worker_alive"])
            self.assertFalse(worker.health_snapshot()["connected"])
        finally:
            worker.stop()
            worker.wait(2000)


if __name__ == "__main__":
    unittest.main()
