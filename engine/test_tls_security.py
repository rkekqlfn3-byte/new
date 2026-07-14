"""Regression tests for verified TLS and certificate error handling."""

import ssl
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from engine.llm_engine import LLMEngine
from engine.network import (
    get_verified_ssl_context,
    is_certificate_verification_error,
)
from engine.runtime_paths import PROJECT_ROOT
from engine.test_ai_router import RouterDictionary
from engine.test_llm_failures import DummyDictionary


class TLSContextTests(unittest.TestCase):
    def test_context_requires_trusted_certificate_and_hostname(self):
        get_verified_ssl_context.cache_clear()
        context = get_verified_ssl_context()
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats().get("x509_ca", 0), 0)

    def test_wrapped_certificate_error_is_recognized(self):
        certificate_error = ssl.SSLCertVerificationError(
            1, "certificate verify failed: self-signed certificate"
        )
        wrapped = urllib.error.URLError(certificate_error)
        self.assertTrue(is_certificate_verification_error(wrapped))
        self.assertFalse(is_certificate_verification_error(urllib.error.URLError("offline")))

    def test_runtime_sources_do_not_disable_global_verification(self):
        for relative_path in ("jarvis_app.py", "engine/llm_engine.py"):
            source = (Path(PROJECT_ROOT) / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("_create_unverified_context", source)
            self.assertNotIn("CERT_NONE", source)


class TLSProviderTests(unittest.TestCase):
    def setUp(self):
        self.engine = LLMEngine(DummyDictionary({
            "provider": "openai",
            "api_key": "key",
            "ollama_model": "llama3",
            "routing_mode": "auto",
        }))
        self.certificate_error = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
        )

    def test_cloud_providers_return_clear_certificate_error(self):
        for provider, call in (
            ("openai", self.engine._call_openai),
            ("gemini", self.engine._call_gemini),
        ):
            with self.subTest(provider=provider), mock.patch(
                "engine.llm_engine.urlopen_verified",
                side_effect=self.certificate_error,
            ):
                result = call("key", "prompt", "input")
            self.assertTrue(result["tls_certificate_error"])
            self.assertEqual("tls_certificate_error", result["error_type"])
            self.assertIn("인증서 검증", result["response"])
            self.assertIn("검증을 끄지 않았습니다", result["response"])

    def test_certificate_failure_does_not_hide_behind_ollama_fallback(self):
        engine = LLMEngine(RouterDictionary(
            routing_mode="auto", api_key="key", provider="openai"
        ))
        failure = {
            "response": "인증서 검증 실패",
            "action": "none",
            "provider_error": True,
            "provider": "openai",
            "tls_certificate_error": True,
        }
        with mock.patch.object(
            engine, "_call_openai", return_value=failure
        ), mock.patch.object(engine, "_call_ollama") as ollama:
            result = engine.process_command(
                "새 명령", mode="command", use_api=False
            )
        self.assertTrue(result["tls_certificate_error"])
        ollama.assert_not_called()


if __name__ == "__main__":
    unittest.main()
