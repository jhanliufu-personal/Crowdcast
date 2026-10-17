"""Regression checks for stale reverse listeners shared by USB and Wi-Fi."""
import unittest
from unittest.mock import patch, call

from serve import refresh_quest_forward


class QuestForwardTest(unittest.TestCase):
    def refresh(self, devices, previous):
        with patch("serve.adb_devices", return_value=devices), patch(
            "serve.adb", return_value="host-34 tcp:8000 tcp:8000\n"
        ) as adb:
            result = refresh_quest_forward(8000, previous)
        return result, adb.call_args_list

    def test_wifi_drop_replaces_stale_mapping_with_usb(self):
        result, calls = self.refresh(["USB"], ("20.20.20.215:5555", "USB"))
        self.assertEqual(result, ("USB",))
        self.assertEqual(calls, [
            call("-s", "USB", "reverse", "--remove", "tcp:8000"),
            call("-s", "USB", "reverse", "tcp:8000", "tcp:8000"),
        ])

    def test_unplug_switches_to_wifi(self):
        _, calls = self.refresh(["20.20.20.215:5555"], ("20.20.20.215:5555", "USB"))
        self.assertEqual(calls[-1], call("-s", "20.20.20.215:5555", "reverse", "tcp:8000", "tcp:8000"))

    def test_stable_connection_is_not_interrupted(self):
        _, calls = self.refresh(["USB"], ("USB",))
        self.assertEqual(calls, [call("-s", "USB", "reverse", "--list")])

    def test_startup_replaces_existing_stale_listener(self):
        _, calls = self.refresh(["USB", "20.20.20.215:5555"], None)
        self.assertEqual(calls[-1], call("-s", "USB", "reverse", "tcp:8000", "tcp:8000"))

    def test_disconnected_headset(self):
        result, calls = self.refresh([], ("USB",))
        self.assertEqual(result, ())
        self.assertEqual(calls, [])

    def test_lost_listener_is_restored_without_transport_change(self):
        with patch("serve.adb_devices", return_value=["USB"]), patch("serve.adb", return_value="") as adb:
            refresh_quest_forward(8000, ("USB",))
        self.assertEqual(adb.call_args_list[-1], call("-s", "USB", "reverse", "tcp:8000", "tcp:8000"))
