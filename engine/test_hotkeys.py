import unittest

from engine.hotkeys import (
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    press_hotkey,
    resolve_key,
)


class HotkeyTests(unittest.TestCase):
    def test_common_aliases_and_function_keys(self):
        self.assertEqual((0x11, False), resolve_key("control"))
        self.assertEqual((0x5B, True), resolve_key("윈도우"))
        self.assertEqual((0x43, False), resolve_key("c"))
        self.assertEqual((0x70, False), resolve_key("F1"))
        self.assertEqual((0x87, False), resolve_key("f24"))

    def test_key_down_and_reverse_key_up_order(self):
        events = []

        press_hotkey("ctrl+c", keybd_event=lambda *args: events.append(args))

        self.assertEqual(
            [
                (0x11, 0, 0, 0),
                (0x43, 0, 0, 0),
                (0x43, 0, KEYEVENTF_KEYUP, 0),
                (0x11, 0, KEYEVENTF_KEYUP, 0),
            ],
            events,
        )

    def test_extended_key_flags_are_preserved(self):
        events = []

        press_hotkey("win+d", keybd_event=lambda *args: events.append(args))

        self.assertEqual(KEYEVENTF_EXTENDEDKEY, events[0][2])
        self.assertEqual(KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, events[-1][2])

    def test_unsupported_key_is_rejected_before_any_keypress(self):
        events = []
        with self.assertRaises(ValueError):
            press_hotkey("ctrl+없는키", keybd_event=lambda *args: events.append(args))
        self.assertEqual([], events)


if __name__ == "__main__":
    unittest.main()
