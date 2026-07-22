import threading
import unittest

from engine.app_actions.base import AppActionUnavailable
from engine.app_actions.com_lifecycle import OfficeApplicationLease, com_apartment
from engine.app_actions.excel_adapter import (
    ExcelAdapter,
    create_owned_excel_application,
)
from engine.app_actions.hwp_adapter import (
    HwpAdapter,
    create_owned_hwp_application,
)


class FakeComRuntime:
    def __init__(self):
        self.initialized = 0
        self.uninitialized = 0
        self._lock = threading.Lock()

    def CoInitialize(self):
        with self._lock:
            self.initialized += 1

    def CoUninitialize(self):
        with self._lock:
            self.uninitialized += 1


class FakeDocument:
    def __init__(self, fail_close=False):
        self.close_calls = []
        self.fail_close = fail_close

    def Close(self, *args, **kwargs):
        self.close_calls.append((args, kwargs))
        if self.fail_close:
            raise RuntimeError("close failed")


class FakeApplication:
    def __init__(self):
        self.quit_calls = 0

    def Quit(self):
        self.quit_calls += 1


class ComOwnershipTests(unittest.TestCase):
    def test_caller_owned_outer_apartment_can_disable_nested_initialization(self):
        with com_apartment(False):
            pass

    def test_attached_excel_is_preserved_and_only_references_are_cleared(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        user_document = FakeDocument()
        lease = OfficeApplicationLease(
            application,
            owns_application=False,
            application_kind="excel",
            owned_documents=[user_document],
        )
        adapter = ExcelAdapter(
            application_getter=lambda: lease,
            process_counter=lambda: 1,
            com_runtime=runtime,
        )

        with adapter._application() as connected:
            self.assertIs(application, connected)

        self.assertEqual(0, application.quit_calls)
        self.assertEqual([], user_document.close_calls)
        self.assertIsNone(lease.application)
        self.assertEqual((1, 1), (runtime.initialized, runtime.uninitialized))

    def test_owned_excel_closes_only_registered_documents_then_quits(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        owned_document = FakeDocument()
        unrelated_document = FakeDocument()
        lease = create_owned_excel_application(lambda _progid: application)
        lease.register_owned_document(owned_document)
        adapter = ExcelAdapter(
            application_getter=lambda: lease,
            process_counter=lambda: 1,
            com_runtime=runtime,
        )

        with adapter._application():
            pass

        self.assertEqual(1, application.quit_calls)
        self.assertEqual([((), {"SaveChanges": False})], owned_document.close_calls)
        self.assertEqual([], unrelated_document.close_calls)
        self.assertTrue(lease.created_by_jarvis)
        self.assertEqual((1, 1), (runtime.initialized, runtime.uninitialized))

    def test_owned_excel_quits_and_uninitializes_after_operation_error(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        document = FakeDocument(fail_close=True)
        lease = create_owned_excel_application(lambda _progid: application)
        lease.register_owned_document(document)
        adapter = ExcelAdapter(
            application_getter=lambda: lease,
            process_counter=lambda: 1,
            com_runtime=runtime,
        )

        with self.assertRaisesRegex(RuntimeError, "operation failed"):
            with adapter._application():
                raise RuntimeError("operation failed")

        self.assertEqual(1, application.quit_calls)
        self.assertEqual((1, 1), (runtime.initialized, runtime.uninitialized))

    def test_100_attached_excel_operations_do_not_create_or_quit_instances(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        created_instances = []

        def attached_getter():
            return application

        adapter = ExcelAdapter(
            application_getter=attached_getter,
            process_counter=lambda: 1,
            com_runtime=runtime,
        )
        for _ in range(100):
            with adapter._application() as connected:
                self.assertIs(application, connected)
                created_instances.append(id(connected))

        self.assertEqual({id(application)}, set(created_instances))
        self.assertEqual(0, application.quit_calls)
        self.assertEqual((100, 100), (runtime.initialized, runtime.uninitialized))

    def test_each_worker_thread_balances_its_own_com_apartment(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        adapter = ExcelAdapter(
            application_getter=lambda: application,
            process_counter=lambda: 1,
            com_runtime=runtime,
        )
        errors = []

        def worker():
            try:
                with adapter._application() as connected:
                    self.assertIs(application, connected)
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors)
        self.assertEqual((20, 20), (runtime.initialized, runtime.uninitialized))

    def test_hwp_attached_and_owned_lifetimes_are_distinct(self):
        runtime = FakeComRuntime()
        user_hwp = FakeApplication()
        attached = HwpAdapter(
            object_getter=lambda: user_hwp,
            com_runtime=runtime,
        )
        with attached._hwp():
            pass
        self.assertEqual(0, user_hwp.quit_calls)

        owned_hwp = FakeApplication()
        document = FakeDocument()
        lease = create_owned_hwp_application(lambda _progid: owned_hwp)
        lease.register_owned_document(document)
        owned = HwpAdapter(
            object_getter=lambda: lease,
            com_runtime=runtime,
        )
        with owned._hwp():
            pass

        self.assertEqual(1, owned_hwp.quit_calls)
        self.assertEqual([((False,), {})], document.close_calls)
        self.assertEqual((2, 2), (runtime.initialized, runtime.uninitialized))

    def test_hwp_exception_still_quits_owned_application_and_uninitializes(self):
        runtime = FakeComRuntime()
        application = FakeApplication()
        lease = create_owned_hwp_application(lambda _progid: application)
        adapter = HwpAdapter(
            object_getter=lambda: lease,
            com_runtime=runtime,
        )

        with self.assertRaisesRegex(RuntimeError, "hwp operation failed"):
            with adapter._hwp():
                raise RuntimeError("hwp operation failed")

        self.assertEqual(1, application.quit_calls)
        self.assertEqual((1, 1), (runtime.initialized, runtime.uninitialized))

    def test_getter_failure_still_balances_com_apartment(self):
        runtime = FakeComRuntime()

        def fail_getter():
            raise RuntimeError("not running")

        adapter = ExcelAdapter(
            application_getter=fail_getter,
            process_counter=lambda: 0,
            com_runtime=runtime,
        )
        with self.assertRaises(AppActionUnavailable):
            with adapter._application():
                pass
        self.assertEqual((1, 1), (runtime.initialized, runtime.uninitialized))

    def test_com_lifecycle_never_uses_manual_oleobj_release(self):
        for module in (
            "engine/app_actions/com_lifecycle.py",
            "engine/app_actions/excel_adapter.py",
            "engine/app_actions/hwp_adapter.py",
        ):
            with open(module, encoding="utf-8") as source:
                text = source.read().casefold()
            self.assertNotIn("oleobj", text)
            self.assertNotIn(".release()", text)


if __name__ == "__main__":
    unittest.main()
