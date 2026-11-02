"""OPC UA monitoring worker; GUI calls never wait for network I/O.

Subscriptions deliver individual transitions in order. Batched reads provide an
initial snapshot and periodically check every point, including unchanged points.
An OPC UA subscription cannot recover events already cleared before connection,
or events discarded by the PLC/server before they reach its monitored-item queue.
"""

import asyncio
import logging
import threading
import time

from asyncua import Client, ua
from PyQt6.QtCore import QThread, pyqtSignal

LOGGER = logging.getLogger(__name__)


def _node_key(node):
    node_id = node.nodeid
    return node_id.to_string() if hasattr(node_id, "to_string") else str(node_id)


class _SubscriptionHandler:
    def __init__(self, owner):
        self.owner = owner
        self.generation = owner._session_generation

    def datachange_notification(self, node, value, data):
        # A synchronous callback preserves order even when one Publish message
        # contains both True and False for the same monitored item.
        tag_name = self.owner._node_to_tag.get(_node_key(node))
        if (
            tag_name is None
            or not self.owner.running
            or self.generation != self.owner._session_generation
            or not self.owner._transport_connected
        ):
            return
        data_value = getattr(getattr(data, "monitored_item", None), "Value", None)
        if data_value is None:
            self.owner._mark_bad([tag_name], "訂閱通知缺少品質資料")
            return
        self.owner._notification_versions[tag_name] = (
            self.owner._notification_versions.get(tag_name, 0) + 1
        )
        self.owner._apply_data_value(tag_name, data_value, origin="subscription")

    def status_change_notification(self, notification):
        if self.generation != self.owner._session_generation:
            return
        status = getattr(notification, "Status", notification)
        if not status.is_good():
            self.owner._subscription_error = f"訂閱狀態異常: {status}"
            self.owner._mark_bad(list(self.owner.nodes), self.owner._subscription_error)
            if self.owner._wake_event is not None:
                self.owner._wake_event.set()


class OPCUAClient(QThread):
    update_signal = pyqtSignal(dict)
    write_signal = pyqtSignal(str, object)
    write_failed_signal = pyqtSignal(str)
    connection_lost_signal = pyqtSignal()
    connection_restored_signal = pyqtSignal()
    health_signal = pyqtSignal(dict)

    def __init__(
        self,
        server_url,
        tag_list,
        *,
        client_factory=None,
        poll_interval=2.0,
        stale_after=8.0,
        request_timeout=4.0,
        chunk_size=32,
    ):
        super().__init__()
        self.server_url = server_url
        self.tag_list = list(tag_list)
        self.running = True
        self.nodes = {}
        self.client = None
        # Kept for the existing popup's optimistic write controls. These never
        # override incoming server alarm/status notifications.
        self.current_values = {}
        self.write_timestamps = {}
        self.write_timeout = 10.0
        self.last_emitted_values = {}
        self.is_connected = False  # all monitored points are usable
        self._client_factory = client_factory or Client
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.request_timeout = request_timeout
        self.chunk_size = max(1, chunk_size)
        self._lock = threading.RLock()
        self._cache = {}
        self._tag_health = {
            tag: {
                "quality": "unknown",
                "last_good_at": None,
                "last_update_at": None,
                "error": "尚未取得伺服器資料",
            }
            for tag, _ in self.tag_list
        }
        self._good_monotonic = {}
        self._transport_connected = False
        self._worker_alive = False
        self._subscription = None
        self._subscription_ready = False
        self._subscription_error = None
        self._initializing = False
        self._notification_versions = {}
        self._node_to_tag = {}
        self._session_generation = 0
        self._last_data_at = None
        self._last_cycle_at = None
        self._fault_announced = False
        self._wake_event = None
        self._main_task = None
        self._health_reason = "OPC UA 尚未連線"
        self.write_signal.connect(self.write_value)

    @property
    def tag_health(self):
        return self.health_snapshot()["tag_health"]

    def health_snapshot(self):
        """Return a thread-safe snapshot suitable for the UI and heartbeat."""
        with self._lock:
            now = time.monotonic()
            tags = {}
            for tag, data in self._tag_health.items():
                entry = dict(data)
                if entry["quality"] == "good" and (
                    now - self._good_monotonic.get(tag, float("-inf"))
                    > self.stale_after
                ):
                    entry["quality"] = "stale"
                    entry["error"] = "資料超過新鮮度期限"
                tags[tag] = entry
            bad = [tag for tag, state in tags.items() if state["quality"] == "bad"]
            stale = [tag for tag, state in tags.items() if state["quality"] == "stale"]
            unknown = [
                tag for tag, state in tags.items() if state["quality"] == "unknown"
            ]
            healthy = (
                bool(tags)
                and all(state["quality"] == "good" for state in tags.values())
                and self._transport_connected
                and self._subscription_ready
                and not self._subscription_error
            )
            reason = self._health_reason
            if not healthy and self._transport_connected and (bad or stale or unknown):
                reason = f"OPC UA 資料異常：品質不良 {len(bad)}、逾時 {len(stale)}、未知 {len(unknown)}"
            return {
                "connected": self._transport_connected,
                "worker_alive": self._worker_alive,
                "worker_running": self._worker_alive,
                "healthy": bool(healthy),
                "monitoring_healthy": bool(healthy),
                "reason": reason,
                "bad_tags": bad,
                "stale_tags": stale,
                "unknown_tags": unknown,
                "tag_health": tags,
                "last_data_at": self._last_data_at,
                "last_cycle_at": self._last_cycle_at,
                "subscription_ready": self._subscription_ready,
            }

    def _publish_health(self):
        snapshot = self.health_snapshot()
        healthy = snapshot["healthy"]
        if healthy and not self.is_connected:
            self.is_connected = True
            self._fault_announced = False
            LOGGER.info("OPC UA monitoring restored: %s points", len(self.nodes))
            self.connection_restored_signal.emit()
        elif (
            not healthy
            and (self.is_connected or not self._fault_announced)
            and not (
                self._initializing
                and self._transport_connected
                and snapshot["unknown_tags"]
                and not snapshot["bad_tags"]
                and not snapshot["stale_tags"]
            )
        ):
            self.is_connected = False
            self._fault_announced = True
            LOGGER.warning("OPC UA monitoring unavailable: %s", snapshot["reason"])
            self.connection_lost_signal.emit()
        self.health_signal.emit(snapshot)

    def _mark_bad(self, tags, reason):
        with self._lock:
            for tag in tags:
                if tag in self._tag_health:
                    self._tag_health[tag].update(
                        quality="bad", last_update_at=time.time(), error=str(reason)
                    )
        LOGGER.warning("OPC UA point failure: count=%s reason=%s", len(tags), reason)
        self._publish_health()

    def _mark_disconnected(self, reason):
        with self._lock:
            self._transport_connected = False
            self._session_generation += 1
            self._subscription_ready = False
            self._health_reason = str(reason)
            for entry in self._tag_health.values():
                entry.update(quality="stale", error=str(reason))
            pending_writes = list(self.current_values)
        for tag in pending_writes:
            self._write_failed(tag, "連線中斷，寫入結果無法確認")
        self._publish_health()

    def _apply_data_value(self, tag, data_value, *, origin, publish_health=True):
        status = getattr(data_value, "StatusCode", None)
        variant = getattr(data_value, "Value", None)
        value = getattr(variant, "Value", None)
        if status is None or not status.is_good() or value is None:
            self._mark_bad([tag], f"{origin}: {status or 'missing status/value'}")
            return False
        # OPC UA's DataValue overflow bit means queued transitions were lost.
        if status.value & 0x80:
            self._subscription_error = "OPC UA 訂閱佇列溢位，需重新同步"
            self._mark_bad([tag], self._subscription_error)
            if self._wake_event is not None:
                self._wake_event.set()
            return False
        with self._lock:
            previous = self.last_emitted_values.get(tag, _MISSING)
            old_quality = self._tag_health[tag]["quality"]
            now = time.time()
            self._cache[tag] = value
            self._good_monotonic[tag] = time.monotonic()
            self._tag_health[tag].update(
                quality="good", last_good_at=now, last_update_at=now, error=None
            )
            self._last_data_at = now
            self.last_emitted_values[tag] = value
            if self.current_values.get(tag, _MISSING) == value:
                self.current_values.pop(tag, None)
                self.write_timestamps.pop(tag, None)
        if previous is _MISSING or previous != value or old_quality != "good":
            LOGGER.info("OPC UA value: tag=%s value=%r origin=%s", tag, value, origin)
            self.update_signal.emit({tag: value})
        if publish_health:
            self._publish_health()
        return True

    def read_value(self, tag_name):
        """Immediate cache read; never block the GUI on a network request."""
        with self._lock:
            health = self.health_snapshot()["tag_health"].get(tag_name)
            if health is None or health["quality"] != "good":
                return None
            return self._cache.get(tag_name)

    def write_value(self, tag_name, value):
        loop = getattr(self, "loop", None)
        if (
            not self.running
            or not self._transport_connected
            or tag_name not in self.nodes
            or loop is None
            or not loop.is_running()
        ):
            self._write_failed(tag_name, "OPC UA 未連線或工作執行緒未執行")
            return
        with self._lock:
            self.current_values[tag_name] = value
            self.write_timestamps[tag_name] = time.time()
        coroutine = self._write_async(tag_name, value)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            self._write_failed(tag_name, "OPC UA 寫入排程失敗")

    def _write_failed(self, tag, reason):
        with self._lock:
            self.current_values.pop(tag, None)
            self.write_timestamps.pop(tag, None)
        LOGGER.error("OPC UA write failed: tag=%s reason=%s", tag, reason)
        self.write_failed_signal.emit(tag)

    async def _write_async(self, tag_name, value):
        try:
            if isinstance(value, bool):
                variant = ua.Variant(value, ua.VariantType.Boolean)
            elif isinstance(value, float):
                variant = ua.Variant(value, ua.VariantType.Float)
            elif isinstance(value, int):
                variant = ua.Variant(value, ua.VariantType.Int32)
            else:
                variant = ua.Variant(value)
            await asyncio.wait_for(
                self.nodes[tag_name].write_value(ua.DataValue(variant)),
                self.request_timeout,
            )
            LOGGER.info("OPC UA write sent: tag=%s value=%r", tag_name, value)
        except Exception as exc:
            self._write_failed(tag_name, exc)

    async def _read_snapshot(self):
        good_count = 0
        pairs = list(self.nodes.items())
        for offset in range(0, len(pairs), self.chunk_size):
            if not self.running:
                break
            chunk = pairs[offset : offset + self.chunk_size]
            versions = {
                tag: self._notification_versions.get(tag, 0) for tag, _ in chunk
            }
            try:
                values = await asyncio.wait_for(
                    self.client.read_attributes([node for _, node in chunk]),
                    timeout=self.request_timeout,
                )
                if len(values) != len(chunk):
                    raise ValueError("批次讀取回傳數量與請求不符")
                for (tag, _), data_value in zip(chunk, values):
                    if self._notification_versions.get(tag, 0) != versions[tag]:
                        # A newer subscription callback already delivered this point.
                        continue
                    good_count += self._apply_data_value(
                        tag, data_value, origin="snapshot", publish_health=False
                    )
            except Exception as exc:
                LOGGER.exception("OPC UA batch read failed")
                self._mark_bad(
                    [
                        tag
                        for tag, _ in chunk
                        if self._notification_versions.get(tag, 0) == versions[tag]
                    ],
                    exc,
                )
        self._last_cycle_at = time.time()
        self._expire_writes()
        self._publish_health()
        return good_count

    def _expire_writes(self):
        with self._lock:
            expired = [
                tag
                for tag, timestamp in list(self.write_timestamps.items())
                if time.time() - timestamp > self.write_timeout
            ]
        for tag in expired:
            self._write_failed(tag, "寫入未於期限內收到伺服器確認")

    async def _create_subscription(self):
        self._subscription = await asyncio.wait_for(
            self.client.create_subscription(200.0, _SubscriptionHandler(self)),
            timeout=self.request_timeout,
        )
        pairs = list(self.nodes.items())
        rejected = []
        for offset in range(0, len(pairs), self.chunk_size):
            chunk = pairs[offset : offset + self.chunk_size]
            handles = await asyncio.wait_for(
                self._subscription.subscribe_data_change(
                    [node for _, node in chunk], queuesize=128, sampling_interval=100.0
                ),
                timeout=self.request_timeout,
            )
            if not isinstance(handles, list) or len(handles) != len(chunk):
                raise RuntimeError("訂閱節點回傳數量與請求不符")
            for (tag, _), handle in zip(chunk, handles):
                if isinstance(handle, ua.StatusCode):
                    rejected.append(tag)
                    self._mark_bad([tag], f"訂閱建立失敗: {handle}")
        if rejected:
            raise RuntimeError(f"{len(rejected)} 個 OPC UA 節點無法訂閱")
        self._subscription_ready = True

    async def _monitor_connection(self):
        self._initializing = True
        self.nodes = {
            tag: self.client.get_node(node_id) for tag, node_id in self.tag_list
        }
        self._node_to_tag = {_node_key(node): tag for tag, node in self.nodes.items()}
        self._notification_versions = {}
        self._subscription_error = None
        try:
            await self._create_subscription()
        except Exception as exc:
            self._subscription_error = f"無法建立完整 OPC UA 訂閱: {exc}"
            self._health_reason = self._subscription_error
            LOGGER.exception("OPC UA subscription setup failed")
        # Subscribe first, then read. This snapshot must not overwrite a callback
        # that arrived while the read request was outstanding.
        try:
            await self._read_snapshot()
        finally:
            self._initializing = False
            self._publish_health()
        if self._subscription_error:
            raise ConnectionError(self._subscription_error)
        while self.running:
            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(), timeout=self.poll_interval
                )
            except asyncio.TimeoutError:
                pass
            if not self.running:
                break
            if self._subscription_error:
                raise ConnectionError(self._subscription_error)
            await asyncio.wait_for(
                self.client.check_connection(), timeout=self.request_timeout
            )
            if not await self._read_snapshot():
                # If all chunks failed, reconnect instead of keeping a live
                # process with a permanently dead monitoring session.
                if not self.health_snapshot()["healthy"]:
                    raise ConnectionError("無任何有效 OPC UA 資料")

    async def _close_connection(self):
        subscription, self._subscription = self._subscription, None
        if subscription is not None:
            try:
                await asyncio.wait_for(subscription.delete(), timeout=1.0)
            except Exception:
                LOGGER.warning("OPC UA subscription cleanup failed", exc_info=True)
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=1.0)
            except Exception:
                LOGGER.warning("OPC UA disconnect failed", exc_info=True)

    async def _run_client(self):
        delay = 2.0
        self._wake_event = asyncio.Event()
        while self.running:
            try:
                self.client = self._client_factory(url=self.server_url)
                await asyncio.wait_for(
                    self.client.connect(), timeout=self.request_timeout
                )
                self._transport_connected = True
                self._health_reason = "OPC UA 資料監看中"
                delay = 2.0
                await self._monitor_connection()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("OPC UA monitoring connection failed; retrying")
                self._mark_disconnected(f"OPC UA 連線或監看失敗: {exc}")
            finally:
                await self._close_connection()
            if self.running:
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(self._wake_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay + 1.0, 10.0)

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._worker_alive = True
        try:
            self._main_task = self.loop.create_task(self._run_client())
            self.loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            if self.running:
                LOGGER.error("OPC UA worker unexpectedly cancelled")
        except BaseException:
            LOGGER.exception("OPC UA worker terminated unexpectedly")
        finally:
            self._worker_alive = False
            self._mark_disconnected("OPC UA 工作執行緒已停止")
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                try:
                    self.loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=2.0,
                        )
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    LOGGER.warning("OPC UA worker task cleanup timed out")
            self.loop.close()

    def stop(self):
        self.running = False
        loop = getattr(self, "loop", None)
        if loop is not None and loop.is_running():

            def cancel():
                if self._wake_event is not None:
                    self._wake_event.set()
                if self._main_task is not None:
                    self._main_task.cancel()

            loop.call_soon_threadsafe(cancel)


_MISSING = object()
