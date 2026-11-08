"""Offline tests using the real worker with fake OPC UA services."""

import asyncio
from datetime import datetime, timedelta, timezone
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
        self.write_results = []
        self.worker.update_signal.connect(self.updates.append)
        self.worker.health_signal.connect(self.health.append)
        self.worker.connection_lost_signal.connect(lambda: self.lost.append(True))
        self.worker.connection_restored_signal.connect(
            lambda: self.restored.append(True)
        )
        self.worker.write_failed_signal.connect(self.write_failed.append)
        self.worker.write_result_signal.connect(
            lambda request_id, tag, success, reason: self.write_results.append(
                (request_id, tag, success, reason)
            )
        )

    async def wait_for_write_results(self, count):
        async def wait():
            while len(self.write_results) < count:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), timeout=2.0)

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

    def test_negotiated_timing_is_unknown_until_wire_response(self):
        timing = self.worker.health_snapshot()["timing"]
        self.assertEqual(timing["requested_sampling_interval_ms"], 100.0)
        self.assertEqual(timing["requested_publishing_interval_ms"], 200.0)
        self.assertIsNone(timing["revised_publishing_interval_ms"])
        self.assertIsNone(timing["revised_sampling_interval_ms_max"])
        self.assertIsNone(timing["revised_queue_size_min"])

    def test_wire_timing_observers_keep_server_results_and_exceptions(self):
        node_id = ua.NodeId("wire-A", 2)
        self.worker._node_to_tag[node_id.to_string()] = "A"
        create_result = SimpleNamespace(RevisedPublishingInterval=500.0)
        update_result = SimpleNamespace(RevisedPublishingInterval=750.0)
        item_result = ua.MonitoredItemCreateResult(
            StatusCode_=ua.StatusCode(),
            MonitoredItemId=99,
            RevisedSamplingInterval=250.0,
            RevisedQueueSize=64,
        )

        async def create_subscription(params, callback=None):
            return create_result

        async def update_subscription(params):
            return update_result

        async def create_monitored_items(params):
            return [item_result]

        service = SimpleNamespace(
            create_subscription=create_subscription,
            update_subscription=update_subscription,
            create_monitored_items=create_monitored_items,
        )
        self.fake.uaclient = service
        self.worker._install_timing_observers()
        params = ua.CreateMonitoredItemsParameters()
        item = ua.MonitoredItemCreateRequest()
        item.ItemToMonitor.NodeId = node_id
        params.ItemsToCreate = [item]

        async def exercise():
            self.assertIs(await service.create_subscription(object()), create_result)
            self.assertIs(await service.update_subscription(object()), update_result)
            result = await service.create_monitored_items(params)
            self.assertIs(result[0], item_result)

        asyncio.run(exercise())
        timing = self.worker.health_snapshot()["timing"]
        self.assertEqual(timing["revised_publishing_interval_ms"], 750.0)
        self.assertEqual(timing["revised_sampling_interval_ms_min"], 250.0)
        self.assertEqual(timing["revised_queue_size_max"], 64)
        timing["revised_monitored_items"]["A"]["queue_size"] = 1
        self.assertEqual(
            self.worker.health_snapshot()["timing"]["revised_queue_size_max"], 64
        )

        async def fail(params):
            raise ConnectionError("wire failure remains a protocol failure")

        other = SimpleNamespace(create_subscription=fail)
        self.fake.uaclient = other
        self.worker._install_timing_observers()
        with self.assertRaises(ConnectionError):
            asyncio.run(other.create_subscription(object()))

    def test_invalid_timing_metadata_never_discards_a_successful_wire_result(self):
        successful_result = object()

        async def create_subscription(params):
            return successful_result

        self.fake.uaclient = SimpleNamespace(create_subscription=create_subscription)
        self.worker._install_timing_observers()
        with self.assertLogs("ui.opcua_client", level="WARNING"):
            result = asyncio.run(self.fake.uaclient.create_subscription(object()))
        self.assertIs(result, successful_result)
        self.assertIsNone(
            self.worker.health_snapshot()["timing"]["revised_publishing_interval_ms"]
        )

    def test_notification_timestamp_age_is_diagnostic_not_a_rejection(self):
        value = ua.DataValue(
            ua.Variant(True),
            ua.StatusCode(),
            SourceTimestamp=datetime.now(timezone.utc) + timedelta(seconds=5),
            ServerTimestamp=datetime.now(timezone.utc) - timedelta(seconds=2),
        )
        self.assertTrue(
            self.worker._apply_data_value("A", value, origin="subscription")
        )
        timing = self.worker.health_snapshot()["timing"]
        self.assertEqual(timing["notification_count"], 1)
        self.assertLess(timing["last_notification_source_timestamp_age_ms"], 0)
        self.assertGreater(timing["last_notification_server_timestamp_age_ms"], 1900)
        self.assertIs(self.worker.read_value("A"), True)

    def test_snapshot_reports_measured_duration_and_start_interval(self):
        async def slow_read(nodes):
            await asyncio.sleep(0.01)

        self.fake.on_read = slow_read
        asyncio.run(self.worker._read_snapshot())
        first = self.worker.health_snapshot()["timing"]
        self.assertGreaterEqual(first["last_snapshot_duration_ms"], 8)
        self.assertIsNone(first["last_snapshot_start_interval_ms"])
        asyncio.run(self.worker._read_snapshot())
        second = self.worker.health_snapshot()["timing"]
        self.assertGreaterEqual(second["last_snapshot_start_interval_ms"], 8)

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

    def test_correlated_write_without_running_loop_reports_failure(self):
        self.assertFalse(self.worker.write_value("A", True, request_id="press"))
        self.assertEqual(len(self.write_results), 1)
        self.assertEqual(self.write_results[0][:3], ("press", "A", False))
        self.assertIn("未連線", self.write_results[0][3])
        self.assertEqual(self.worker._pending_write_requests, {})

    def test_correlated_write_reports_server_acceptance_with_boolean_value(self):
        async def scenario():
            self.worker.loop = asyncio.get_running_loop()
            self.assertTrue(self.worker.write_value("A", True, request_id="press"))
            await self.wait_for_write_results(1)

        asyncio.run(scenario())
        self.assertEqual(self.write_results, [("press", "A", True, "")])
        variant = self.worker.nodes["A"].writes[0].Value
        self.assertIs(variant.Value, True)
        self.assertEqual(variant.VariantType, ua.VariantType.Boolean)
        self.assertEqual(self.worker._pending_write_requests, {})

    def test_correlated_write_reports_server_failure(self):
        async def scenario():
            self.worker.loop = asyncio.get_running_loop()
            self.worker.nodes["A"].fail_write = True
            self.assertTrue(self.worker.write_value("A", True, request_id="press"))
            await self.wait_for_write_results(1)

        asyncio.run(scenario())
        self.assertEqual(self.write_results[0][:3], ("press", "A", False))
        self.assertIn("synthetic write failure", self.write_results[0][3])
        self.assertEqual(self.write_failed, ["A"])

    def test_correlated_write_schedule_failure_reports_result_and_closes_coroutine(self):
        async def scenario():
            self.worker.loop = asyncio.get_running_loop()
            with patch(
                "ui.opcua_client.asyncio.run_coroutine_threadsafe",
                side_effect=RuntimeError("synthetic stopped loop"),
            ) as schedule:
                self.assertFalse(
                    self.worker.write_value("A", True, request_id="press")
                )
            coroutine = schedule.call_args.args[0]
            self.assertIsNone(coroutine.cr_frame)

        asyncio.run(scenario())
        self.assertEqual(self.write_results[0][:3], ("press", "A", False))
        self.assertIn("排程失敗", self.write_results[0][3])

    def test_press_write_finishes_before_release_write_reaches_same_tag(self):
        seen = []

        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()
            self.worker.loop = asyncio.get_running_loop()

            async def write(value):
                seen.append(value.Value.Value)
                if value.Value.Value:
                    entered.set()
                    await release.wait()

            self.worker.nodes["A"].write_value = write
            self.worker.write_value("A", True, request_id="press")
            await asyncio.wait_for(entered.wait(), 2)
            self.worker.write_value("A", False, request_id="release")
            await asyncio.sleep(0.01)
            self.assertEqual(seen, [True])
            release.set()
            await self.wait_for_write_results(2)

        asyncio.run(scenario())
        self.assertEqual(seen, [True, False])
        self.assertEqual(
            self.write_results,
            [("press", "A", True, ""), ("release", "A", True, "")],
        )

    def test_blocked_room_write_does_not_block_another_room(self):
        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()
            self.worker.loop = asyncio.get_running_loop()

            async def write(value):
                entered.set()
                await release.wait()

            self.worker.nodes["A"].write_value = write
            self.worker.write_value("A", True, request_id="room-a")
            await asyncio.wait_for(entered.wait(), 2)
            self.worker.write_value("B", False, request_id="room-b")
            await self.wait_for_write_results(1)
            self.assertEqual(self.write_results, [("room-b", "B", True, "")])
            release.set()
            await self.wait_for_write_results(2)

        asyncio.run(scenario())
        self.assertEqual(self.write_results[-1], ("room-a", "A", True, ""))

    def test_reconnect_rejects_queued_writes_and_old_session_success(self):
        old_writes = []
        replacement = FakeNode("replacement-A")

        async def scenario():
            entered = asyncio.Event()
            release = asyncio.Event()
            self.worker.loop = asyncio.get_running_loop()

            async def write(value):
                old_writes.append(value.Value.Value)
                entered.set()
                await release.wait()

            self.worker.nodes["A"].write_value = write
            self.worker.write_value("A", True, request_id="press")
            await asyncio.wait_for(entered.wait(), 2)
            self.worker.write_value("A", False, request_id="release")
            await asyncio.sleep(0.01)
            self.worker._mark_disconnected("synthetic reconnect")
            self.worker._transport_connected = True
            self.worker.nodes["A"] = replacement
            release.set()
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(old_writes, [True])
        self.assertEqual(replacement.writes, [])
        self.assertEqual(
            [result[:3] for result in self.write_results],
            [("press", "A", False), ("release", "A", False)],
        )
        self.assertEqual(self.worker._pending_write_requests, {})

    def test_session_change_during_server_write_cannot_report_success(self):
        async def scenario():
            self.worker.loop = asyncio.get_running_loop()

            async def write(value):
                self.worker._session_generation += 1

            self.worker.nodes["A"].write_value = write
            self.worker.write_value("A", True, request_id="press")
            await self.wait_for_write_results(1)

        asyncio.run(scenario())
        self.assertEqual(self.write_results[0][:3], ("press", "A", False))
        self.assertIn("連線已變更", self.write_results[0][3])

    def test_cancelled_write_reports_correlated_failure(self):
        async def scenario():
            entered = asyncio.Event()
            self.worker.loop = asyncio.get_running_loop()

            async def write(value):
                entered.set()
                await asyncio.Event().wait()

            self.worker.nodes["A"].write_value = write
            self.worker.write_value("A", True, request_id="press")
            await asyncio.wait_for(entered.wait(), 2)
            tasks = [
                task for task in asyncio.all_tasks()
                if task.get_coro().__name__ == "_write_async"
            ]
            self.assertEqual(len(tasks), 1)
            tasks[0].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run(scenario())
        self.assertEqual(self.write_results[0][:3], ("press", "A", False))
        self.assertIn("已取消", self.write_results[0][3])

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
