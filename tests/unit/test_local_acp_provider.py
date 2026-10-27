import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from core import config as cfg, providers
from core.errors import AcpError, ConfigurationError, InvalidParameter, LocalAcpForbidden
from core.providers.acp import ClaudeAcpProvider, CodexAcpProvider
from core.providers.base import ImagePart

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/local_acp_agent.py"


@unittest.skipUnless(os.name == "posix", "本機 ACP 目前只支援 POSIX")
class LocalAcpProviderTests(unittest.TestCase):
    def command(self, mode="normal"):
        return json.dumps([sys.executable, str(_FIXTURE), mode])

    def test_round_trip_uses_context_and_separates_sessions(self):
        with patch.object(cfg, "CODEX_ACP_COMMAND", self.command()), patch.dict(os.environ,
                {"ADO_PAT": "synthetic-secret", "OPENAI_API_KEY": "synthetic-key"}):
            provider = providers.resolve("codex_acp", model="model-from-viewer")
            first = json.loads(provider.generate("Mention與Reference Space的合成對話", system="指定輸出格式"))
            second = json.loads(provider.generate("另一份合成對話"))
        self.assertIn("Mention與Reference Space的合成對話", first["prompt"])
        self.assertIn("指定輸出格式", first["prompt"])
        self.assertEqual(first["model"], "model-from-viewer")
        self.assertFalse(first["secret_env"])
        self.assertEqual(first["permission"], "cancelled")
        self.assertEqual(first["fs_error"], -32601)
        self.assertNotEqual(first["session"], second["session"])
        self.assertNotEqual(first["cwd"], second["cwd"])
        self.assertFalse(Path(first["cwd"]).exists())
        with patch.object(cfg, "CLAUDE_ACP_COMMAND", self.command()):
            result = json.loads(ClaudeAcpProvider().generate("合成對話"))
        self.assertIsNone(result["model"])
        self.assertEqual(result["meta"]["claudeCode"]["options"]["tools"], [])
        self.assertEqual(result["meta"]["claudeCode"]["options"]["settingSources"], [])

    def test_error_eof_and_incomplete_output_are_not_success(self):
        for mode in ("error", "eof", "wrongstop", "empty", "nonzero", "malformed"):
            with self.subTest(mode=mode), patch.object(cfg, "CODEX_ACP_COMMAND", self.command(mode)):
                with self.assertRaises(AcpError) as caught:
                    CodexAcpProvider().generate("合成對話")
                self.assertNotIn("SYNTHETIC_PRIVATE_ERROR", str(caught.exception))

    def test_timeout_and_iterator_close_stop_the_child(self):
        with patch.object(cfg, "CODEX_ACP_COMMAND", self.command("timeout")), patch.object(cfg, "ACP_TIMEOUT", "0.05"):
            with self.assertRaises(AcpError):
                CodexAcpProvider().generate("合成對話")
        with patch.object(cfg, "CODEX_ACP_COMMAND", self.command("linger")):
            stream = CodexAcpProvider().stream_text("合成對話")
            started = json.loads(next(stream))
            stream.close()
        with self.assertRaises(ProcessLookupError):
            os.kill(started["pid"], 0)
        self.assertFalse(Path(started["cwd"]).exists())

    def test_dashboard_requires_the_configured_owner(self):
        from dashboard.api import server
        with patch.object(server, "resolve_model", return_value=("codex_acp", None)):
            with patch.object(cfg, "LOCAL_ACP_VIEWER_ID", ""):
                with self.assertRaises(ConfigurationError):
                    server.get_provider(7, "codex_acp")
            with patch.object(cfg, "LOCAL_ACP_VIEWER_ID", "7"):
                for other in (None, 8):
                    with self.assertRaises(LocalAcpForbidden):
                        server.get_provider(other, "codex_acp")
                self.assertEqual(server.get_provider(7, "codex_acp").name, "codex_acp")
        described = {item["name"]: item for item in providers.describe_all()}
        self.assertIn("codex_acp", described)
        self.assertIn("claude_acp", described)
        self.assertFalse(described["codex_acp"]["supports_vision"])
        self.assertEqual(providers._ALIASES["auto"], ["claude_cli", "gemini"])

    def test_invalid_configuration_and_images_are_rejected(self):
        with patch.object(cfg, "CODEX_ACP_COMMAND", '"not-an-argv"'):
            with self.assertRaises(ConfigurationError):
                CodexAcpProvider()
        with patch.object(cfg, "CODEX_ACP_COMMAND", self.command()):
            with self.assertRaises(InvalidParameter):
                CodexAcpProvider().generate("合成對話", images=[ImagePart("image/png", b"fake")])


if __name__ == "__main__":
    unittest.main()
