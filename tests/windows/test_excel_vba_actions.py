import tempfile
import unittest
from pathlib import Path

from engine.app_actions.base import (
    AppActionBlocked,
    AppActionContextChanged,
    AppActionVerificationError,
)
from engine.app_actions.excel_vba_adapter import (
    ExcelVbaAdapter,
    VbaTrustAccessBlocked,
    vba_trust_status,
)


class FakeCodeModule:
    def __init__(self, code):
        self.code = str(code)
        self.fail_next_add = False
        self.fail_add_count = 0

    @property
    def CountOfLines(self):
        return len(self.code.splitlines())

    def Lines(self, start, count):
        return self.code

    def DeleteLines(self, start, count):
        self.code = ""

    def AddFromString(self, code):
        if self.fail_add_count > 0:
            self.fail_add_count -= 1
            raise RuntimeError("injected repeated write failure")
        if self.fail_next_add:
            self.fail_next_add = False
            raise RuntimeError("injected write failure")
        self.code = str(code)


class FakeComponent:
    Type = 1

    def __init__(self, name, code):
        self.Name = name
        self.CodeModule = FakeCodeModule(code)
        self.exports = []
        self.fail_export = False

    def Export(self, path):
        if self.fail_export:
            raise RuntimeError("injected backup failure")
        target = Path(path)
        target.write_text(self.CodeModule.code or "' empty", encoding="utf-8")
        self.exports.append(target)


class FakeComponents:
    def __init__(self, *components):
        self.components = list(components)

    @property
    def Count(self):
        return len(self.components)

    def Item(self, value):
        if isinstance(value, int):
            return self.components[value - 1]
        for component in self.components:
            if component.Name == value:
                return component
        raise KeyError(value)


class FakeProject:
    Protection = 0
    Name = "VBAProject"

    def __init__(self, *components):
        self.VBComponents = FakeComponents(*components)


class FakeWorkbook:
    ReadOnly = False
    HasVBProject = True

    def __init__(self, path, project):
        self.FullName = str(path)
        self.Path = str(Path(path).parent)
        self.Name = Path(path).name
        self.VBProject = project


class FakeApplication:
    Hwnd = 9001

    def __init__(self, workbook):
        self.ActiveWorkbook = workbook
        self.ActiveSheet = type("Sheet", (), {"Name": "Sheet1"})()
        self.runs = []

    def Run(self, macro):
        self.runs.append(macro)


def fixture(path, code, backup_dir):
    component = FakeComponent("Module1", code)
    workbook = FakeWorkbook(path, FakeProject(component))
    application = FakeApplication(workbook)
    adapter = ExcelVbaAdapter(
        application_getter=lambda: application,
        process_counter=lambda: 1,
        discovery_retry_delay=0,
        backup_dir=backup_dir,
    )
    return adapter, application, workbook, component


class ExcelVbaActionTests(unittest.TestCase):
    def test_access_vbom_registry_status_is_read_only_and_structured(self):
        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Registry:
            HKEY_CURRENT_USER = "HKCU"
            HKEY_LOCAL_MACHINE = "HKLM"
            KEY_READ = 1
            KEY_WOW64_64KEY = 2
            KEY_WOW64_32KEY = 4

            @staticmethod
            def OpenKey(root, path, reserved, access):
                if (
                    root == "HKCU"
                    and "Policies" not in path
                    and "16.0" in path
                ):
                    return Key()
                raise FileNotFoundError(path)

            @staticmethod
            def QueryValueEx(key, name):
                if name != "AccessVBOM":
                    raise FileNotFoundError(name)
                return 0, 4

        status = vba_trust_status(Registry)
        self.assertEqual("disabled", status["status"])
        self.assertEqual("user_setting", status["source"])
        self.assertEqual("16.0", status["office_version"])
        self.assertTrue(status["runtime_check_required"])
        self.assertFalse(status["security_setting_changed"])

    def test_vba_trust_block_has_explicit_non_retryable_environment_error(self):
        class TrustBlockedWorkbook:
            ReadOnly = False
            HasVBProject = True

            def __init__(self, path):
                self.FullName = str(path)
                self.Path = str(path.parent)
                self.Name = path.name

            @property
            def VBProject(self):
                raise Exception(
                    -2147352567,
                    "Visual Basic project programmatic access denied",
                    (0, "Microsoft Excel", "프로그래밍 방식으로 액세스할 수 없습니다", "", 0, -2146827284),
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "blocked.xlsm"
            path.write_bytes(b"fixture")
            workbook = TrustBlockedWorkbook(path)
            application = FakeApplication(workbook)
            adapter = ExcelVbaAdapter(
                application_getter=lambda: application,
                process_counter=lambda: 1,
                discovery_retry_delay=0,
                backup_dir=Path(temp_dir) / "backups",
            )

            with self.assertRaises(VbaTrustAccessBlocked) as captured:
                adapter.prepare(
                    "vba_inspect_project", {"document_path": str(path)}
                )

            error = captured.exception
            self.assertEqual("environment_error", error.error_type)
            self.assertEqual("blocked", error.status)
            self.assertFalse(error.retryable)
            self.assertFalse(error.state_changed)
            self.assertEqual("vba_trust_access", error.blocked_reason)
            self.assertFalse(
                error.diagnostic_context["security_setting_changed"]
            )

    def test_project_module_and_procedure_inspection_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            code = "Public Sub ExportReport()\nRange(\"A1\").Value = 1\nEnd Sub"
            adapter, _, _, component = fixture(path, code, Path(temp_dir) / "backups")

            project = adapter.execute(adapter.prepare(
                "vba_inspect_project", {"document_path": str(path)}
            ))
            module = adapter.execute(adapter.prepare(
                "vba_read_module",
                {"document_path": str(path), "module_name": "Module1"},
            ))
            analysis = adapter.execute(adapter.prepare(
                "vba_analyze_module",
                {"document_path": str(path), "module_name": "Module1"},
            ))

            self.assertEqual(1, project["module_count"])
            self.assertEqual(code, module["code"])
            self.assertEqual("ExportReport", analysis["procedures"][0]["name"])
            self.assertEqual([], component.exports)

    def test_module_change_exports_backup_verifies_and_can_restore(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            original = (
                "Public Sub FindLast()\n"
                "lastRow = Cells(Rows.Count, 1).End(xlUp).Row\n"
                "End Sub"
            )
            adapter, _, _, component = fixture(path, original, Path(temp_dir) / "backups")
            prepared = adapter.prepare(
                "vba_replace_module",
                {
                    "document_path": str(path),
                    "module_name": "Module1",
                    "change_kind": "fix_last_row",
                },
            )
            result = adapter.execute(prepared)

            self.assertTrue(result["verified"])
            self.assertTrue(Path(result["backup_path"]).is_file())
            self.assertEqual(1, len(component.exports))
            self.assertIn("ActiveSheet.Cells(ActiveSheet.Rows.Count", component.CodeModule.code)

            restored = adapter.undo(prepared)
            self.assertTrue(restored["verified"])
            self.assertEqual(original, component.CodeModule.code)

    def test_write_failure_restores_original_after_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            original = "Public Sub Test()\nRange(\"A1\") = 1\nEnd Sub"
            adapter, _, _, component = fixture(path, original, Path(temp_dir) / "backups")
            prepared = adapter.prepare(
                "vba_replace_module",
                {
                    "document_path": str(path),
                    "module_name": "Module1",
                    "replacement_code": original.replace("= 1", "= 2"),
                },
            )
            component.CodeModule.fail_next_add = True

            with self.assertRaises(AppActionVerificationError):
                adapter.execute(prepared)
            self.assertEqual(original, component.CodeModule.code)
            self.assertEqual(1, len(component.exports))

    def test_failed_write_and_failed_restore_keep_verified_external_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            original = "Public Sub Test()\nRange(\"A1\") = 1\nEnd Sub"
            adapter, _, _, component = fixture(
                path, original, Path(temp_dir) / "backups"
            )
            prepared = adapter.prepare(
                "vba_replace_module",
                {
                    "document_path": str(path),
                    "module_name": "Module1",
                    "replacement_code": original.replace("= 1", "= 2"),
                },
            )
            component.CodeModule.fail_add_count = 2

            with self.assertRaises(AppActionVerificationError) as captured:
                adapter.execute(prepared)

            self.assertIn("자동 복원에 실패", str(captured.exception))
            self.assertEqual(1, len(component.exports))
            self.assertTrue(component.exports[0].is_file())
            self.assertEqual(original, component.exports[0].read_text(encoding="utf-8"))

    def test_changed_code_or_failed_backup_never_mutates_module(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            original = "Public Sub Test()\nRange(\"A1\") = 1\nEnd Sub"
            adapter, _, _, component = fixture(path, original, Path(temp_dir) / "backups")
            prepared = adapter.prepare(
                "vba_replace_module",
                {
                    "document_path": str(path),
                    "module_name": "Module1",
                    "replacement_code": original.replace("= 1", "= 2"),
                },
            )

            component.CodeModule.code += "\n' user change"
            with self.assertRaises(AppActionContextChanged):
                adapter.execute(prepared)
            self.assertEqual([], component.exports)

            component.CodeModule.code = original
            component.fail_export = True
            with self.assertRaises(RuntimeError):
                adapter.execute(prepared)
            self.assertEqual(original, component.CodeModule.code)

    def test_xlsb_is_supported_and_project_absence_is_reported_read_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsb"
            path.write_bytes(b"fixture")
            adapter, _, workbook, _ = fixture(
                path, "Public Sub Test()\nEnd Sub", Path(temp_dir) / "backups"
            )
            inspected = adapter.execute(adapter.prepare(
                "vba_inspect_project", {"document_path": str(path)}
            ))
            self.assertTrue(inspected["has_vba_project"])

            workbook.HasVBProject = False
            empty = adapter.execute(adapter.prepare(
                "vba_inspect_project", {"document_path": str(path)}
            ))
            self.assertFalse(empty["has_vba_project"])
            self.assertEqual([], empty["modules"])

    def test_run_requires_unchanged_public_parameterless_sub(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stage9.xlsm"
            path.write_bytes(b"fixture")
            code = "Public Sub ExportReport()\nRange(\"A1\") = 1\nEnd Sub"
            adapter, application, _, component = fixture(
                path, code, Path(temp_dir) / "backups"
            )
            prepared = adapter.prepare(
                "vba_run_procedure",
                {
                    "document_path": str(path),
                    "module_name": "Module1",
                    "procedure_name": "ExportReport",
                },
            )
            result = adapter.execute(prepared)
            self.assertTrue(result["invocation_completed"])
            self.assertEqual("invocation_return", result["verification_scope"])
            self.assertFalse(result["business_result_verified"])
            self.assertNotIn("macro_completed", result)
            self.assertEqual(["'stage9.xlsm'!Module1.ExportReport"], application.runs)

            component.CodeModule.code += "\n' changed"
            with self.assertRaises(AppActionContextChanged):
                adapter.execute(prepared)

    def test_unsupported_read_only_and_locked_projects_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            xlsx = Path(temp_dir) / "plain.xlsx"
            xlsx.write_bytes(b"fixture")
            adapter, _, workbook, _ = fixture(
                xlsx, "Sub Test()\nEnd Sub", Path(temp_dir) / "backups"
            )
            with self.assertRaises(AppActionBlocked):
                adapter.prepare("vba_inspect_project", {"document_path": str(xlsx)})

            xlsm = Path(temp_dir) / "readonly.xlsm"
            xlsm.write_bytes(b"fixture")
            adapter, _, workbook, _ = fixture(
                xlsm, "Sub Test()\nEnd Sub", Path(temp_dir) / "backups"
            )
            workbook.ReadOnly = True
            with self.assertRaises(AppActionBlocked):
                adapter.prepare(
                    "vba_replace_module",
                    {
                        "document_path": str(xlsm),
                        "module_name": "Module1",
                        "replacement_code": "Sub Test()\nEnd Sub",
                    },
                )

            workbook.ReadOnly = False
            workbook.VBProject.Protection = 1
            with self.assertRaises(AppActionBlocked):
                adapter.prepare(
                    "vba_read_module",
                    {"document_path": str(xlsm), "module_name": "Module1"},
                )


if __name__ == "__main__":
    unittest.main()
