import asyncio
import time
import unittest
import unittest.mock

from duosida_ev import (
    ChargerState,
    CommandRejectedError,
    ConfigurationStatus,
    DuosidaClient,
    DuosidaConnectionError,
    DuosidaTimeoutError,
    ValidationError,
)
from duosida_ev import protocol as p
from duosida_ev.client import CONFIGURATION_TIMEOUT
from duosida_ev.protobuf import Message, string_field, varint_field
from duosida_ev.protocol import Action

from .helpers import DEVICE_ID, MESSAGES, FakeCharger, frame, status_body


class ClientTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.charger = await FakeCharger().__aenter__()
        self.client = self.make_client()

    async def asyncTearDown(self) -> None:
        await self.client.disconnect()
        await self.charger.__aexit__()

    def make_client(self, **kwargs: object) -> DuosidaClient:
        options: dict[str, object] = {
            "timeout": 1.0,
            "keepalive_interval": None,
            "idle_timeout": None,
        }
        options.update(kwargs)
        return DuosidaClient("127.0.0.1", self.charger.port, **options)  # type: ignore[arg-type]

    async def wait_until(self, predicate, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
        async with asyncio.timeout(timeout):
            while not predicate():  # noqa: ASYNC110 - polling client state is the point
                await asyncio.sleep(0.01)


class ConnectTest(ClientTestCase):
    async def test_connect_identifies_and_sets_clock(self) -> None:
        info = await self.client.connect()
        self.assertTrue(self.client.connected)
        self.assertEqual(info.device_id, DEVICE_ID)
        self.assertEqual(info.model, "DUOSIDA Mode3@32A")
        self.assertEqual(self.client.device_id, DEVICE_ID)
        self.assertIs(self.client.info, info)

        handshake = self.charger.received[0]
        self.assertEqual(
            p.encode_frame(
                handshake.field, handshake.body, handshake.charge_box_id, handshake.message_id
            ),
            MESSAGES["app_handshake"],
        )
        boot_conf = await self.charger.expect(Action.BOOT_NOTIFICATION.confirmation)
        self.assertEqual(boot_conf.message_id, frame("boot_notification").message_id)
        self.assertEqual(boot_conf.charge_box_id, DEVICE_ID)
        sent_time = Message.decode(boot_conf.body).require(1).as_sint()
        self.assertLess(abs(sent_time - time.time()), 5)

    async def test_connect_twice_is_a_no_op(self) -> None:
        first = await self.client.connect()
        self.assertIs(await self.client.connect(), first)
        self.assertEqual(self.charger.connections, 1)

    async def test_context_manager(self) -> None:
        async with self.make_client() as client:
            self.assertTrue(client.connected)
        self.assertFalse(client.connected)

    async def test_falls_back_to_id_without_boot_notification(self) -> None:
        self.charger.send_boot = False
        info = await self.make_client(timeout=0.3).connect()
        self.assertEqual(info.device_id, DEVICE_ID)
        self.assertEqual(info.model, "")

    async def test_confirms_boot_unasked_without_boot_notification(self) -> None:
        # Live, twice after a reconnect, the charger accepted the trigger but
        # sent neither its BootNotification nor any status. 0.1 sent a
        # BootNotification.conf after every trigger, asked for or not.
        self.charger.send_boot = False
        self.charger.trigger_conf = p.encode_frame(
            Action.TRIGGER_MESSAGE.confirmation, status_body(0), DEVICE_ID, 4242
        )
        client = self.make_client(timeout=0.3)
        try:
            with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
                await client.connect()
            boot_conf = await self.charger.expect(Action.BOOT_NOTIFICATION.confirmation)
        finally:
            await client.disconnect()
        self.assertEqual(boot_conf.charge_box_id, DEVICE_ID)
        # In the capture the charger's BootNotification.req has the id just
        # below its answer to the trigger.
        self.assertEqual(boot_conf.message_id, 4241)
        body = Message.decode(boot_conf.body)
        self.assertLess(abs(body.require(1).as_sint() - time.time()), 5)
        self.assertEqual(body.require(2).as_int(), 0)  # interval
        self.assertEqual(body.require(3).as_int(), 0)  # Accepted
        warnings = [line for line in logs.output if line.startswith("WARNING")]
        self.assertIn("confirming the boot unasked", warnings[0])
        sent = "BootNotification.conf #4241 (unasked)"
        self.assertTrue(any(sent in line for line in logs.output))

    async def test_unasked_boot_confirmation_without_trigger_answer_uses_id_0(self) -> None:
        # The id can come from any message, here a Heartbeat.req instead of
        # the trigger's answer.
        self.charger.send_boot = False
        self.charger.trigger_conf = MESSAGES["heartbeat_req"]
        client = self.make_client(timeout=0.3)
        try:
            await client.connect()
            boot_conf = await self.charger.expect(Action.BOOT_NOTIFICATION.confirmation)
        finally:
            await client.disconnect()
        self.assertEqual(boot_conf.message_id, 0)

    async def test_no_unasked_boot_confirmation_after_disconnect(self) -> None:
        self.charger.send_boot = False
        client = self.make_client(timeout=0.3)
        connecting = asyncio.create_task(client.connect())
        await self.charger.expect(Action.TRIGGER_MESSAGE.request)
        with self.assertNoLogs("duosida_ev.client", "WARNING"):
            await client.disconnect()
            with self.assertRaises(DuosidaConnectionError):
                await connecting
        await self.wait_until(lambda: len(self.charger.closures) == 1)
        self.assertNotIn(
            Action.BOOT_NOTIFICATION.confirmation, [f.field for f in self.charger.received]
        )

    async def test_boot_trigger_answer_is_logged(self) -> None:
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            await self.client.connect()
        self.assertTrue(
            any("BootNotification trigger answered 0 (Accepted)" in line for line in logs.output)
        )

    async def test_rejected_boot_trigger_is_in_the_warning(self) -> None:
        # Live, after a restart the charger answered the trigger for 12 min
        # without ever sending its BootNotification; the log couldn't say why.
        self.charger.send_boot = False
        self.charger.trigger_conf = p.encode_frame(
            Action.TRIGGER_MESSAGE.confirmation, status_body(1), DEVICE_ID, 4242
        )
        client = self.make_client(timeout=0.3)
        try:
            with self.assertLogs("duosida_ev.client", "WARNING") as logs:
                await client.connect()
        finally:
            await client.disconnect()
        self.assertIn("no BootNotification (trigger answered 1 (Rejected))", logs.output[0])

    async def test_reconnect_to_silent_charger_does_not_reuse_old_id(self) -> None:
        await self.client.connect()
        await self.client.disconnect()
        self.charger.answers.clear()
        self.charger._react = lambda received: None  # type: ignore[method-assign]
        client = self.client
        client.timeout = 0.3
        with self.assertRaises(DuosidaTimeoutError):
            await client.connect()
        self.assertFalse(client.connected)
        self.assertEqual(client.device_id, DEVICE_ID)  # still known from before

    async def test_refused(self) -> None:
        port = self.charger.port
        await self.charger.__aexit__()
        with self.assertRaises(DuosidaConnectionError):
            await DuosidaClient("127.0.0.1", port, timeout=1.0).connect()

    async def test_silent_peer_times_out(self) -> None:
        held: list[asyncio.StreamWriter] = []
        server = await asyncio.start_server(lambda r, w: held.append(w), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            client = DuosidaClient("127.0.0.1", port, timeout=0.3)
            with self.assertRaises(DuosidaTimeoutError):
                await client.connect()
            self.assertFalse(client.connected)
        finally:
            for writer in held:
                writer.close()
            server.close()
            await server.wait_closed()

    async def test_not_connected(self) -> None:
        with self.assertRaises(DuosidaConnectionError):
            await self.client.start_charging()
        with self.assertRaises(DuosidaConnectionError):
            await self.client.wait_for_status()


class PushTest(ClientTestCase):
    async def test_status_push_updates_and_is_acknowledged(self) -> None:
        await self.client.connect()
        calls: list[ChargerState | None] = []
        remove = self.client.add_listener(
            lambda: calls.append(self.client.status.state if self.client.status else None)
        )
        waiting = asyncio.create_task(self.client.wait_for_status())
        await asyncio.sleep(0)
        self.charger.push(MESSAGES["status_charging"])
        status = await waiting
        self.assertIs(status.state, ChargerState.CHARGING)
        self.assertIs(self.client.status, status)
        self.assertEqual(self.client.transaction_id, 1741610)
        self.assertEqual(calls, [ChargerState.CHARGING])
        # Same acknowledgement bytes as the vendor app sent for this message.
        ack = await self.charger.expect(Action.DATA_TRANSFER.confirmation)
        app_ack = frame("app_data_transfer_conf")
        self.assertEqual((ack.body, ack.message_id), (app_ack.body, app_ack.message_id))
        remove()
        remove()  # removing twice is harmless

    async def test_get_status_waits_then_uses_cache(self) -> None:
        self.charger.after_boot = (MESSAGES["status_available"],)
        await self.client.connect()
        first = await self.client.get_status(timeout=1.0)
        self.assertIs(first.state, ChargerState.AVAILABLE)
        self.assertIs(await self.client.get_status(), first)

    async def test_status_notification(self) -> None:
        await self.client.connect()
        self.charger.push(MESSAGES["status_notification_preparing"])
        await self.wait_until(lambda: self.client.connector_status is not None)
        assert self.client.connector_status is not None
        self.assertIs(self.client.connector_status.state, ChargerState.PREPARING)
        conf = await self.charger.expect(Action.STATUS_NOTIFICATION.confirmation)
        self.assertEqual(conf.body, b"")

    async def test_heartbeat_gets_current_time(self) -> None:
        await self.client.connect()
        self.charger.push(MESSAGES["heartbeat_req"])
        conf = await self.charger.expect(Action.HEARTBEAT.confirmation)
        self.assertEqual(conf.message_id, frame("heartbeat_req").message_id)
        self.assertLess(abs(Message.decode(conf.body).require(1).as_int() - time.time()), 5)

    async def test_transactions(self) -> None:
        await self.client.connect()
        self.charger.push(MESSAGES["start_transaction_req"])
        conf = await self.charger.expect(Action.START_TRANSACTION.confirmation)
        assigned = Message.decode(conf.body).require(2).as_int()
        self.assertEqual(self.client.transaction_id, assigned)
        self.charger.push(MESSAGES["meter_values"])
        await self.charger.expect(Action.METER_VALUES.confirmation)
        self.assertEqual(self.client.transaction_id, 1741610)
        self.charger.push(MESSAGES["stop_transaction_req"])
        await self.charger.expect(Action.STOP_TRANSACTION.confirmation)
        self.assertIsNone(self.client.transaction_id)

    async def test_passive_mode_only_answers_boot(self) -> None:
        client = self.make_client(respond_to_charger=False)
        await client.connect()
        try:
            await self.charger.expect(Action.BOOT_NOTIFICATION.confirmation)
            for name in ("status_charging", "heartbeat_req", "start_transaction_req"):
                self.charger.push(MESSAGES[name])
            await self.wait_until(lambda: client.status is not None)
            await asyncio.sleep(0.1)
            self.assertEqual(
                [f.field for f in self.charger.received],
                [Action.TRIGGER_MESSAGE.request, Action.BOOT_NOTIFICATION.confirmation],
            )
        finally:
            await client.disconnect()

    async def test_garbage_message_is_skipped(self) -> None:
        await self.client.connect()
        # A well-framed message whose action is not length-delimited.
        bad = varint_field(16, 1) + string_field(100, DEVICE_ID) + varint_field(101, 7)
        self.charger.push(bad + MESSAGES["status_finishing"])
        await self.wait_until(lambda: self.client.status is not None)
        self.assertTrue(self.client.connected)

    async def test_listener_errors_do_not_break_reader(self) -> None:
        await self.client.connect()

        def broken() -> None:
            raise RuntimeError("boom")

        self.client.add_listener(broken)
        with self.assertLogs("duosida_ev.client", "ERROR"):
            self.charger.push(MESSAGES["status_finishing"])
            await self.wait_until(lambda: self.client.status is not None)
        self.assertTrue(self.client.connected)


class CommandTest(ClientTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.client.connect()

    async def test_start_charging(self) -> None:
        await self.client.start_charging()
        request = await self.charger.expect(Action.REMOTE_START_TRANSACTION.request)
        self.assertEqual(request.body, frame("app_remote_start").body)
        self.assertEqual(request.charge_box_id, DEVICE_ID)

    async def test_start_rejected(self) -> None:
        self.charger.answers[Action.REMOTE_START_TRANSACTION.request] = lambda f: status_body(1)
        with self.assertRaises(CommandRejectedError) as caught:
            await self.client.start_charging()
        self.assertEqual(caught.exception.status, 1)
        self.assertIn("Rejected", str(caught.exception))

    async def test_stop_uses_active_transaction(self) -> None:
        self.charger.push(MESSAGES["status_charging"])
        await self.wait_until(lambda: self.client.transaction_id is not None)
        await self.client.stop_charging()
        request = await self.charger.expect(Action.REMOTE_STOP_TRANSACTION.request)
        self.assertEqual(request.body, frame("app_remote_stop").body)

    async def test_stop_without_transaction_warns(self) -> None:
        with self.assertLogs("duosida_ev.client", "WARNING"):
            await self.client.stop_charging()
        await self.client.stop_charging(transaction_id=42)
        requests = [f for f in self.charger.received if f.field == 36]
        self.assertEqual(Message.decode(requests[-1].body).require(1).as_int(), 42)

    async def test_set_max_current(self) -> None:
        self.assertIs(await self.client.set_max_current(16), ConfigurationStatus.ACCEPTED)
        request = await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
        self.assertEqual(request.body, p.change_configuration("VendorMaxWorkCurrent", "16"))

    async def test_every_setter(self) -> None:
        calls = [
            (self.client.set_connection_timeout(120), "ConnectionTimeOut", "120"),
            (self.client.set_max_temperature(90), "VendorMaxWorkTemperature", "90"),
            (self.client.set_max_voltage(280), "VendorMaxWorkVoltage", "280"),
            (self.client.set_min_voltage(80), "VendorMinWorkVoltage", "80"),
            (self.client.set_direct_work_mode(True), "VendorDirectWorkMode", "1"),
            (self.client.set_led_brightness(3), "VendorLEDStrength", "3"),
            (self.client.set_stop_on_disconnect(False), "StopTransactionOnEVSideDisconnect", "0"),
        ]
        for call, key, value in calls:
            await call
            request = await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
            self.assertEqual(request.body, p.change_configuration(key, value))

    async def test_reboot_required_is_returned(self) -> None:
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: status_body(2)
        self.assertIs(await self.client.set_config("X", "1"), ConfigurationStatus.REBOOT_REQUIRED)

    async def test_rejected_configuration(self) -> None:
        for code in (1, 3, 9):
            answer = lambda f, c=code: status_body(c)  # noqa: E731
            self.charger.answers[Action.CHANGE_CONFIGURATION.request] = answer
            with self.subTest(code=code), self.assertRaises(CommandRejectedError):
                await self.client.set_max_current(10)

    async def test_validation(self) -> None:
        for call in (
            lambda: self.client.set_max_current(5),
            lambda: self.client.set_max_current(33),
            lambda: self.client.set_max_current(True),  # type: ignore[arg-type]
            lambda: self.client.set_max_current(16.0),  # type: ignore[arg-type]
            lambda: self.client.set_connection_timeout(10),
            lambda: self.client.set_max_temperature(100),
            lambda: self.client.set_max_voltage(300),
            lambda: self.client.set_min_voltage(50),
            lambda: self.client.set_led_brightness(2),
        ):
            with self.assertRaises(ValidationError):
                await call()
        self.assertFalse([f for f in self.charger.received if f.field == 10])

    async def test_unanswered_request_is_retransmitted(self) -> None:
        # Seen live: the charger sometimes ignores a request; the vendor app
        # sends it again with the same message id.
        seen: list[int] = []

        def flaky(request):  # type: ignore[no-untyped-def]
            seen.append(request.message_id)
            return None if len(seen) == 1 else status_body(0)

        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = flaky
        self.client.retry_interval = 0.1
        self.assertIs(await self.client.set_max_current(8), ConfigurationStatus.ACCEPTED)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1])

    async def test_no_retransmission_when_disabled(self) -> None:
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: None
        self.client.retry_interval = None
        self.client.timeout = 0.3
        with self.assertRaises(DuosidaTimeoutError):
            await self.client.set_max_current(8)
        self.assertEqual(len([f for f in self.charger.received if f.field == 10]), 1)

    async def test_no_answer_times_out(self) -> None:
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: None
        with self.assertRaises(DuosidaTimeoutError):
            await self.client.set_max_current(16)
        self.assertTrue(self.client.connected)

    async def test_configuration_read_gets_more_time_than_commands(self) -> None:
        # Live, the charger answered some pulls only 5-6 s after the first
        # send, past the timeout that commands use.
        sends: list[int] = []
        answer = self.charger.answers[Action.DATA_TRANSFER.request]

        def slow(request):  # type: ignore[no-untyped-def]
            sends.append(request.message_id)
            return answer(request) if len(sends) >= 5 else None

        self.charger.answers[Action.DATA_TRANSFER.request] = slow
        client = self.make_client(timeout=0.2, retry_interval=0.1)
        await client.connect()
        try:
            config = await client.get_configuration()
        finally:
            await client.disconnect()
        self.assertEqual(config.max_current, 16.0)
        self.assertEqual(len(set(sends)), 1)  # one request, resent with its id

    async def test_configuration_timeout_default_and_longer_client_timeout(self) -> None:
        for client_timeout, expected in ((1.0, CONFIGURATION_TIMEOUT), (20.0, 20.0)):
            client = self.make_client(timeout=client_timeout)
            seen: list[float | None] = []

            async def fake_request(action, body, timeout=None, seen=seen):  # type: ignore[no-untyped-def]
                seen.append(timeout)
                raise DuosidaTimeoutError("no answer")

            with (
                unittest.mock.patch.object(client, "_request", fake_request),
                self.assertRaises(DuosidaTimeoutError),
            ):
                await client.get_configuration()
            self.assertEqual(seen, [expected])

    async def test_explicit_configuration_timeout_is_kept(self) -> None:
        self.charger.answers[Action.DATA_TRANSFER.request] = lambda f: None
        started = time.monotonic()
        with self.assertRaises(DuosidaTimeoutError):
            await self.client.get_configuration(timeout=0.2)
        self.assertLess(time.monotonic() - started, 1.0)

    async def test_late_answer_is_logged(self) -> None:
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: None
        client = self.make_client(timeout=0.2, retry_interval=None)
        await client.connect()
        try:
            with self.assertRaises(DuosidaTimeoutError):
                await client.set_max_current(8)
            request = await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
            conf = p.encode_frame(
                Action.CHANGE_CONFIGURATION.confirmation,
                status_body(0),
                DEVICE_ID,
                request.message_id,
            )
            with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
                self.charger.push(conf)
                await self.wait_until(lambda: any("late answer" in x for x in logs.output))
        finally:
            await client.disconnect()
        line = next(x for x in logs.output if "late answer" in x)
        self.assertIn(f"CHANGE_CONFIGURATION #{request.message_id}", line)
        self.assertRegex(line, r"\d+\.\d s after it was sent")

    async def test_duplicate_answer_is_logged(self) -> None:
        # Live, an answered resend brought a second conf for the same id.
        await self.client.set_max_current(8)
        request = await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
        conf = p.encode_frame(
            Action.CHANGE_CONFIGURATION.confirmation, status_body(0), DEVICE_ID, request.message_id
        )
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            self.charger.push(conf)
            await self.wait_until(lambda: any("duplicate answer" in x for x in logs.output))
        line = next(x for x in logs.output if "duplicate answer" in x)
        self.assertIn(f"CHANGE_CONFIGURATION #{request.message_id}", line)

    async def test_two_answers_in_one_read_are_logged(self) -> None:
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: None
        await self.client.connect()
        writing = asyncio.create_task(self.client.set_max_current(8))
        request = await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
        conf = p.encode_frame(
            Action.CHANGE_CONFIGURATION.confirmation, status_body(0), DEVICE_ID, request.message_id
        )
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            self.charger.push(conf + conf)
            await writing
            await self.wait_until(lambda: any("duplicate answer" in x for x in logs.output))

    async def test_get_configuration(self) -> None:
        config = await self.client.get_configuration()
        self.assertEqual(config.max_current, 16.0)
        request = await self.charger.expect(Action.DATA_TRANSFER.request)
        self.assertEqual(request.body, frame("app_pull_configuration").body)

    async def test_concurrent_commands(self) -> None:
        results = await asyncio.gather(
            self.client.set_max_current(10),
            self.client.set_max_voltage(280),
            self.client.start_charging(),
        )
        self.assertEqual(results[:2], [ConfigurationStatus.ACCEPTED] * 2)
        ids = [f.message_id for f in self.charger.received if f.field in (10, 34)]
        self.assertEqual(len(set(ids)), 3)


class ConnectionLossTest(ClientTestCase):
    async def test_drop_fails_pending_and_notifies(self) -> None:
        await self.client.connect()
        self.charger.answers[Action.CHANGE_CONFIGURATION.request] = lambda f: None
        changes: list[bool] = []
        self.client.add_listener(lambda: changes.append(self.client.connected))
        pending = asyncio.create_task(self.client.set_max_current(16))
        await self.charger.expect(Action.CHANGE_CONFIGURATION.request)
        await self.charger.drop()
        with self.assertRaises(DuosidaConnectionError):
            await pending
        self.assertFalse(self.client.connected)
        self.assertIn(False, changes)
        # A new connect() starts over.
        await self.client.connect()
        self.assertTrue(self.client.connected)
        self.assertEqual(self.charger.connections, 2)

    async def test_status_waiter_fails_on_drop(self) -> None:
        await self.client.connect()
        waiting = asyncio.create_task(self.client.wait_for_status())
        await asyncio.sleep(0)
        await self.charger.drop()
        with self.assertRaises(DuosidaConnectionError):
            await waiting

    async def test_idle_timeout_drops_connection(self) -> None:
        client = self.make_client(idle_timeout=0.2)
        await client.connect()
        try:
            await self.wait_until(lambda: not client.connected)
        finally:
            await client.disconnect()

    async def test_keepalive_sends_heartbeat_trigger(self) -> None:
        client = self.make_client(keepalive_interval=0.05, idle_timeout=5.0)
        await client.connect()
        try:
            with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
                trigger = await self.charger.expect(Action.TRIGGER_MESSAGE.request)
                while trigger.message_id == 0:  # skip the handshake
                    trigger = await self.charger.expect(Action.TRIGGER_MESSAGE.request)
                await self.wait_until(lambda: any("(keep-alive)" in x for x in logs.output))
                answer = f"<< TriggerMessage.conf #{trigger.message_id}"
                await self.wait_until(lambda: any(answer in x for x in logs.output))
            self.assertEqual(trigger.body, frame("app_trigger_heartbeat").body)
            self.assertTrue(client.connected)
        finally:
            await client.disconnect()
        self.assertTrue(
            any(f"TRIGGER_MESSAGE.req #{trigger.message_id} (keep-alive)" in x for x in logs.output)
        )
        # Its answers are expected, not "late" or "unknown".
        self.assertFalse([x for x in logs.output if "answers no request" in x or "late" in x])

    async def test_fatal_error_closes_gracefully(self) -> None:
        self.charger.linger = True
        await self.client.connect()
        self.charger.push(b"\x0f\x00")  # wire type 7: the stream cannot be framed
        await self.wait_until(lambda: not self.client.connected)
        await self.wait_until(lambda: self.charger.closures)
        self.assertEqual(self.charger.closures, ["eof"])

    async def test_disconnect_during_connect_leaves_no_session(self) -> None:
        real_open = asyncio.open_connection

        async def slow_open(*args, **kwargs):  # type: ignore[no-untyped-def]
            await asyncio.sleep(0.2)
            return await real_open(*args, **kwargs)

        with unittest.mock.patch("asyncio.open_connection", slow_open):
            connecting = asyncio.create_task(self.client.connect())
            await asyncio.sleep(0.05)  # connect() is inside open_connection
            await self.client.disconnect()
            with self.assertRaises(DuosidaConnectionError):
                await connecting
        self.assertFalse(self.client.connected)
        await self.wait_until(lambda: self.charger.closures)
        self.assertEqual(len(self.charger.closures), self.charger.connections)

    async def test_disconnect_sends_fin_and_drains(self) -> None:
        self.charger.linger = True
        await self.client.connect()
        # Data that arrives after our FIN must be read, not answered with a RST.
        self.charger.push(MESSAGES["status_charging"] + MESSAGES["data_continue"])
        await self.client.disconnect()
        await self.wait_until(lambda: self.charger.closures)
        self.assertEqual(self.charger.closures, ["eof"])

    async def test_disconnect_logs_the_close(self) -> None:
        await self.client.connect()
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            await self.client.disconnect()
        text = "\n".join(logs.output)
        self.assertIn("closing (disconnected)", text)
        self.assertIn("FIN sent", text)
        self.assertIn("while closing: charger closed the connection", text)
        self.assertRegex(text, r"reader finished \d+\.\d s after our FIN")
        self.assertTrue(logs.output[-1].endswith(": closed"))

    async def test_disconnect_logs_a_charger_that_never_sends_fin(self) -> None:
        # The real charger never answers our FIN with its own.
        self.charger.close_on_eof = False
        client = self.make_client(timeout=0.3)
        await client.connect()
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            await client.disconnect()
        self.assertTrue(any("no FIN from the charger within 0.3 s" in x for x in logs.output))

    async def test_fatal_error_logs_the_close(self) -> None:
        await self.client.connect()
        with self.assertLogs("duosida_ev.client", "DEBUG") as logs:
            self.charger.push(b"\x0f\x00")  # wire type 7: the stream cannot be framed
            await self.wait_until(lambda: any(": closed" in x for x in logs.output))
        text = "\n".join(logs.output)
        self.assertIn("connection lost", text)
        self.assertIn("FIN sent", text)

    async def test_disconnect_is_idempotent(self) -> None:
        await self.client.connect()
        await self.client.disconnect()
        await self.client.disconnect()
        self.assertFalse(self.client.connected)
        self.assertIsNotNone(self.client.info)


if __name__ == "__main__":
    unittest.main()
