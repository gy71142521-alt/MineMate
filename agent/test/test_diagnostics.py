import logging
import unittest

from pydantic import BaseModel, ValidationError

from app import diagnostics


class SampleModel(BaseModel):
    count: int


class DiagnosticsTests(unittest.TestCase):
    def test_redacts_known_and_common_credentials(self):
        text = diagnostics.redact(
            "key=unit-secret Bearer abc.def sk-live-123 password=hunter2",
            secrets=("unit-secret",),
        )
        self.assertNotIn("unit-secret", text)
        self.assertNotIn("abc.def", text)
        self.assertNotIn("sk-live-123", text)
        self.assertNotIn("hunter2", text)
        self.assertIn("[已隐藏]", text)

    def test_safe_validation_error_never_echoes_input(self):
        try:
            SampleModel.model_validate({"count": "unit-secret"})
        except ValidationError as error:
            text = diagnostics.safe_validation_error(error, {"count"})
        self.assertIn("count", text)
        self.assertNotIn("unit-secret", text)

    def test_log_error_keeps_exception_chain_and_redacts(self):
        inner = ValueError("inner unit-secret")
        try:
            raise RuntimeError("outer") from inner
        except RuntimeError as error:
            with self.assertLogs("minemate.test", level="ERROR") as captured:
                diagnostics.log_error(
                    logging.getLogger("minemate.test"), "处理失败", error,
                    secrets=("unit-secret",), player="Steve", attempt=2,
                )
        text = "\n".join(captured.output)
        self.assertIn("RuntimeError: outer", text)
        self.assertIn("ValueError: inner", text)
        self.assertIn("Traceback", text)
        self.assertIn("player=Steve", text)
        self.assertIn("attempt=2", text)
        self.assertNotIn("unit-secret", text)

    def test_log_error_can_emit_summary_only(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError as error:
            with self.assertLogs("minemate.test", level="ERROR") as captured:
                diagnostics.log_error(
                    logging.getLogger("minemate.test"), "处理失败", error,
                    include_traceback=False, code="E_TEST",
                )
        text = "\n".join(captured.output)
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("code=E_TEST", text)
        self.assertNotIn("Traceback", text)

    def test_all_mode_forces_traceback_for_expected_errors(self):
        diagnostics.set_traceback_mode("all")
        try:
            try:
                raise ValueError("expected validation")
            except ValueError as error:
                with self.assertLogs("minemate.test", level="ERROR") as captured:
                    diagnostics.log_error(
                        logging.getLogger("minemate.test"), "校验失败", error,
                        include_traceback=False, code="EXPECTED",
                    )
        finally:
            diagnostics.set_traceback_mode("errors")
        self.assertIn("Traceback", "\n".join(captured.output))

    def test_model_preview_only_in_all_mode_and_is_bounded(self):
        diagnostics.set_traceback_mode("errors")
        try:
            self.assertIsNone(diagnostics.model_preview({"x": 1}))
            diagnostics.set_traceback_mode("all")
            preview = diagnostics.model_preview({"arguments": {"x": 1}, "key": "unit-secret"}, ("unit-secret",), limit=80)
        finally:
            diagnostics.set_traceback_mode("errors")
        self.assertIsNotNone(preview)
        self.assertIn('"x": 1', preview or "")
        self.assertNotIn("unit-secret", preview or "")
        self.assertLessEqual(len(preview or ""), 90)

    def test_request_id_round_trip_and_reset(self):
        token = diagnostics.set_request_id("req-12345678")
        try:
            self.assertEqual(diagnostics.current_request_id(), "req-12345678")
        finally:
            diagnostics.reset_request_id(token)
        self.assertEqual(diagnostics.current_request_id(), "-")
