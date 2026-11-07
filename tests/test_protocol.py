import unittest
from datetime import UTC, datetime

from duosida_ev import ChargerState
from duosida_ev import protocol as p
from duosida_ev.exceptions import ProtocolError
from duosida_ev.framing import FrameSplitter
from duosida_ev.protobuf import Message, string_field, varint_field
from duosida_ev.protocol import Action, TriggerTarget

from .helpers import DEVICE_ID, MESSAGES, STREAMS, frame


def vendor_status(name: str):  # type: ignore[no-untyped-def]
    transfer = p.parse_data_transfer(frame(name).body)
    assert transfer.data is not None
    return p.parse_vendor_status(transfer.data.as_bytes())


class ActionTest(unittest.TestCase):
    def test_numbering(self) -> None:
        expected = {
            Action.BOOT_NOTIFICATION: (3, 4),
            Action.CHANGE_CONFIGURATION: (9, 10),
            Action.DATA_TRANSFER: (15, 16),
            Action.HEARTBEAT: (29, 30),
            Action.METER_VALUES: (31, 32),
            Action.REMOTE_START_TRANSACTION: (33, 34),
            Action.REMOTE_STOP_TRANSACTION: (35, 36),
            Action.START_TRANSACTION: (45, 46),
            Action.STATUS_NOTIFICATION: (47, 48),
            Action.STOP_TRANSACTION: (49, 50),
            Action.TRIGGER_MESSAGE: (51, 52),
        }
        for action, (conf, req) in expected.items():
            self.assertEqual((action.confirmation, action.request), (conf, req))

    def test_every_captured_message_is_known(self) -> None:
        for stream in STREAMS.values():
            for raw in FrameSplitter().feed(stream):
                decoded = p.decode_frame(raw)
                self.assertIsNotNone(decoded.action, decoded.describe())
                self.assertEqual(
                    p.encode_frame(
                        decoded.field, decoded.body, decoded.charge_box_id, decoded.message_id
                    ),
                    raw,
                )

    def test_describe(self) -> None:
        self.assertEqual(frame("heartbeat_req").describe().split(" ")[0], "Heartbeat.req")
        self.assertTrue(
            frame("remote_start_conf").describe().startswith("RemoteStartTransaction.conf")
        )
        unknown = p.Frame(field=200, body=b"", charge_box_id="", message_id=1)
        self.assertIsNone(unknown.action)
        self.assertEqual(unknown.describe(), "field200.req #1")


class BuildersMatchVendorAppTest(unittest.TestCase):
    """What the library sends is byte-identical to what the vendor app sent."""

    def test_handshake(self) -> None:
        self.assertEqual(p.handshake(), MESSAGES["app_handshake"])

    def test_boot_notification_conf(self) -> None:
        app = frame("app_boot_notification_conf")
        now = Message.decode(app.body).require(1).as_sint()
        self.assertEqual(datetime.fromtimestamp(now, UTC).isoformat(), "2025-11-18T15:10:10+00:00")
        self.assertEqual(p.boot_notification_conf(now), app.body)
        self.assertEqual(app.message_id, frame("boot_notification").message_id)

    def test_heartbeat_conf(self) -> None:
        app = frame("app_heartbeat_conf")
        now = Message.decode(app.body).require(1).as_int()
        self.assertEqual(datetime.fromtimestamp(now, UTC).date().isoformat(), "2025-11-18")
        self.assertEqual(p.heartbeat_conf(now), app.body)

    def test_data_transfer_conf(self) -> None:
        # The app acknowledged the first DataVendorStatusReq (data in field 10) with field 11.
        app = frame("app_data_transfer_conf")
        self.assertEqual(app.message_id, frame("status_charging").message_id)
        self.assertEqual(p.data_transfer_conf(10), app.body)
        self.assertEqual(p.data_transfer_conf(None), varint_field(1, 0))

    def test_start_transaction_conf(self) -> None:
        app = frame("app_start_transaction_conf")
        transaction_id = Message.decode(app.body).require(2).as_int()
        self.assertEqual(p.start_transaction_conf(transaction_id), app.body)

    def test_remote_start(self) -> None:
        self.assertEqual(p.remote_start_transaction(), frame("app_remote_start").body)

    def test_remote_stop(self) -> None:
        self.assertEqual(p.remote_stop_transaction(1741610), frame("app_remote_stop").body)

    def test_pull_configuration(self) -> None:
        self.assertEqual(p.pull_all_configuration(), frame("app_pull_configuration").body)

    def test_change_configuration(self) -> None:
        body = p.change_configuration("VendorMaxWorkVoltage", "265")
        self.assertEqual(body, frame("app_change_configuration").body)

    def test_trigger_heartbeat(self) -> None:
        body = p.trigger_message(TriggerTarget.HEARTBEAT)
        self.assertEqual(body, frame("app_trigger_heartbeat").body)


class ParserTest(unittest.TestCase):
    def test_boot_notification(self) -> None:
        received = frame("boot_notification")
        info = p.parse_boot_notification(received.body, received.charge_box_id)
        self.assertEqual(info.device_id, DEVICE_ID)
        self.assertEqual(info.model, "DUOSIDA Mode3@32A")
        self.assertEqual(info.manufacturer, "UCHEN")
        # 0.1.x read "...+102": the trailing "2" was the next field's tag byte.
        self.assertEqual(info.firmware, "V2.5@46ab776+266,F13.wifi2,mq,V1.0@7b79f88+10")

    def test_status_while_charging(self) -> None:
        status = vendor_status("status_charging")
        self.assertIs(status.state, ChargerState.CHARGING)
        self.assertTrue(status.charging)
        self.assertTrue(status.vehicle_connected)
        self.assertAlmostEqual(status.voltage, 219.6, places=1)
        self.assertAlmostEqual(status.current, 15.13, places=2)
        self.assertAlmostEqual(status.power, status.voltage * status.current)
        self.assertAlmostEqual(status.total_energy, 26.8075, places=3)
        self.assertAlmostEqual(status.session_energy, 1.1358, places=3)
        self.assertAlmostEqual(status.cp_voltage, 5.948, places=2)
        self.assertEqual(status.transaction_id, 1741610)
        self.assertEqual(status.id_tag, "DirectWorkUser")
        # The session began with the charger's clock wound back to the 0.1.x
        # hard-coded time (2025-11-17 17:21:19): this is the session_time bug.
        assert status.session_start is not None
        self.assertEqual(status.session_start.isoformat(), "2025-11-17T17:22:56+00:00")
        self.assertTrue(status.unknown)

    def test_status_finishing(self) -> None:
        status = vendor_status("status_finishing")
        self.assertIs(status.state, ChargerState.FINISHING)
        self.assertTrue(status.vehicle_connected)  # CP 8.8 V
        self.assertIsNone(status.transaction_id)
        self.assertIsNone(status.session_start)
        self.assertIsNone(status.id_tag)
        self.assertIsNone(status.session_duration)

    def test_status_available(self) -> None:
        status = vendor_status("status_available")
        self.assertIs(status.state, ChargerState.AVAILABLE)
        self.assertFalse(status.vehicle_connected)  # CP 11.8 V
        self.assertAlmostEqual(status.total_energy, 29.5275, places=3)
        self.assertEqual(status.current, 0.0)

    def test_status_suspended_ev(self) -> None:
        self.assertIs(vendor_status("status_suspended_ev").state, ChargerState.SUSPENDED_EV)

    def test_status_notification(self) -> None:
        notification = p.parse_status_notification(frame("status_notification_preparing").body)
        self.assertEqual(notification.connector_id, 1)
        self.assertIs(notification.state, ChargerState.PREPARING)
        self.assertEqual(notification.error_code, 6)
        self.assertFalse(notification.has_error)
        self.assertIsNone(notification.info)

    def test_transaction_ids(self) -> None:
        self.assertEqual(p.meter_values_transaction_id(frame("meter_values").body), 1741610)
        self.assertEqual(p.stop_transaction_id(frame("stop_transaction_req").body), 1741610)
        self.assertIsNone(p.meter_values_transaction_id(b""))
        self.assertIsNone(p.stop_transaction_id(b""))

    def test_data_continue_is_not_status(self) -> None:
        transfer = p.parse_data_transfer(frame("data_continue").body)
        self.assertEqual(transfer.vendor_id, p.VENDOR_ID)
        self.assertEqual(transfer.message_id, p.DataMessage.CONTINUE)
        self.assertEqual(transfer.data_field, 12)

    def test_configuration(self) -> None:
        config = p.parse_configuration(frame("configuration_conf").body)
        self.assertEqual(config.max_temperature, 90.0)
        self.assertEqual(config.max_voltage, 280.0)
        self.assertEqual(config.max_current, 16.0)
        self.assertEqual(config.min_voltage, 80.0)
        self.assertEqual(
            config.raw[32], "Core,LocalAuthListManagement,Reservation,SmartCharging,RemoteTrigger"
        )
        as_dict = config.to_dict()
        self.assertEqual(as_dict["provisional"]["max_current"], 16.0)
        self.assertEqual(as_dict["raw"]["6"], "")

    def test_configuration_redacts_passwords(self) -> None:
        from duosida_ev import ChargerConfiguration

        config = ChargerConfiguration(raw={3: 16.0, 7: "hunter2", 8: "MySSID", 9: b"", 10: 1})
        self.assertEqual(config.raw[7], "hunter2")
        self.assertEqual(config.redacted()[7], "<redacted>")
        self.assertEqual(config.redacted()[8], "<redacted>")  # SSID
        self.assertEqual(config.redacted()[9], b"")  # empty: nothing to hide
        self.assertNotIn("hunter2", str(config.to_dict()))
        self.assertNotIn("MySSID", str(config.to_dict()))

    def test_configuration_errors(self) -> None:
        with self.assertRaises(ProtocolError):
            p.parse_configuration(varint_field(1, 1))
        with self.assertRaises(ProtocolError):
            p.parse_configuration(varint_field(1, 0))

    def test_confirmation_status(self) -> None:
        self.assertEqual(p.conf_status(frame("change_configuration_conf").body), 0)
        self.assertEqual(p.conf_status(frame("remote_start_conf").body), 0)
        self.assertEqual(p.conf_status(b""), 0)
        self.assertEqual(p.conf_status(varint_field(1, 1)), 1)

    def test_status_without_state_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            p.parse_vendor_status(varint_field(1, 0))

    def test_unknown_state_code(self) -> None:
        self.assertIs(ChargerState.from_code(8), ChargerState.FAULTED)
        self.assertIs(ChargerState.from_code(42), ChargerState.UNKNOWN)

    def test_decode_frame_errors(self) -> None:
        with self.assertRaises(ProtocolError):
            p.decode_frame(b"")
        with self.assertRaises(ProtocolError):
            p.decode_frame(string_field(100, DEVICE_ID) + varint_field(101, 1))
        with self.assertRaises(ProtocolError):
            p.decode_frame(varint_field(16, 1) + varint_field(101, 1))  # action must be LEN


class StatusModelTest(unittest.TestCase):
    def test_to_dict_and_str(self) -> None:
        status = vendor_status("status_charging")
        data = status.to_dict()
        self.assertEqual(data["state"], "Charging")
        self.assertEqual(data["transaction_id"], 1741610)
        self.assertEqual(data["session_start"], "2025-11-17T17:22:56+00:00")
        self.assertGreater(data["session_duration"], 0)
        text = str(status)
        self.assertIn("Charging", text)
        self.assertIn("Transaction:", text)

    def test_vehicle_connected_without_cp(self) -> None:
        from dataclasses import replace

        status = replace(vendor_status("status_available"), cp_voltage=0.0)
        self.assertFalse(status.vehicle_connected)
        status = replace(status, state=ChargerState.SUSPENDED_EV)
        self.assertTrue(status.vehicle_connected)


if __name__ == "__main__":
    unittest.main()
