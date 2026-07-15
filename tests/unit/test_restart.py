import unittest
from unittest.mock import patch

from engine.api import dictionary_api


class RestartJarvisTests(unittest.TestCase):
    def test_frozen_restart_requests_a_fresh_onefile_environment(self):
        with (
            patch.object(dictionary_api.sys, "frozen", True, create=True),
            patch.object(dictionary_api.sys, "executable", r"C:\Jarvis\Jarvis.exe"),
            patch.dict(
                dictionary_api.os.environ,
                {"_PYI_APPLICATION_HOME_DIR": r"C:\Temp\_MEI-old"},
                clear=True,
            ),
            patch.object(dictionary_api.subprocess, "Popen") as popen,
            patch.object(dictionary_api.os, "_exit") as exit_process,
        ):
            dictionary_api.restart_jarvis()

        popen.assert_called_once()
        command = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual([r"C:\Jarvis\Jarvis.exe"], command)
        self.assertEqual("1", environment["PYINSTALLER_RESET_ENVIRONMENT"])
        self.assertEqual(
            r"C:\Temp\_MEI-old", environment["_PYI_APPLICATION_HOME_DIR"]
        )
        exit_process.assert_called_once_with(0)

    def test_source_restart_keeps_script_arguments_without_reset_flag(self):
        with (
            patch.object(dictionary_api.sys, "frozen", False, create=True),
            patch.object(dictionary_api.sys, "executable", "python.exe"),
            patch.object(dictionary_api.sys, "argv", ["jarvis_app.py", "--probe"]),
            patch.dict(dictionary_api.os.environ, {}, clear=True),
            patch.object(dictionary_api.subprocess, "Popen") as popen,
            patch.object(dictionary_api.os, "_exit") as exit_process,
        ):
            dictionary_api.restart_jarvis()

        popen.assert_called_once_with(
            ["python.exe", "jarvis_app.py", "--probe"], env={}
        )
        exit_process.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
