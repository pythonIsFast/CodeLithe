from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codelite.app.server import create_app
from codelite.config import AppConfig
from codelite.permission.manager import PermissionDenied, PermissionManager
from codelite.permission.modes import Mode
from codelite.project.context import build_project_context, discover_skills, read_skill
from codelite.tools.code_intelligence import CODE_INTELLIGENCE
from codelite.tools.base import ToolError
from codelite.tools.context import ToolContext
from codelite.tools.extensions import EXTENSIONS
from codelite.tools.memory import PROJECT_MEMORY

FIXTURES = Path(__file__).parent / "fixtures"


class FakeSession:
    pass


class ProjectIntelligenceTests(unittest.TestCase):
    def context(self, workspace: Path, data_dir: Path) -> ToolContext:
        return ToolContext(
            workspace=workspace,
            permissions=PermissionManager(Mode.BYPASS, lambda *_: None),
            session=FakeSession(),
            data_dir=data_dir,
            model="fixture",
        )

    def test_memory_and_lazy_skills(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            data_dir = Path(root) / "data"
            workspace.mkdir()
            context = self.context(workspace, data_dir)
            PROJECT_MEMORY.run(
                {"action": "append", "content": "- Test with `python -m unittest`."},
                context,
            )
            PROJECT_MEMORY.run(
                {
                    "action": "append",
                    "scope": "global",
                    "content": "- Always keep responses concise.",
                },
                context,
            )
            skill_dir = workspace / ".codelite" / "skills" / "review"
            skill_dir.mkdir(parents=True)
            skill_dir.joinpath("SKILL.md").write_text(
                "---\nname: review\ndescription: Review changes carefully.\n---\n\n# Review\nRead the diff.",
                encoding="utf-8",
            )
            prompt = build_project_context(workspace, data_dir)
            self.assertIn("python -m unittest", prompt)
            self.assertIn("Global memory", prompt)
            self.assertIn("Always keep responses concise", prompt)
            self.assertIn("review: Review changes carefully.", prompt)
            self.assertEqual(discover_skills(workspace, data_dir)[0].name, "review")
            self.assertIn("Read the diff", read_skill(workspace, data_dir, "review"))

    def test_global_memory_settings_apply_without_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            app = create_app(AppConfig(data_dir=data_dir))
            client = app.test_client()

            saved = client.put(
                "/api/settings/memory",
                json={"content": "- Prefer small, dependency-free changes.\n"},
            )
            loaded = client.get("/api/settings")

            self.assertEqual(saved.status_code, 200)
            self.assertEqual(loaded.status_code, 200)
            self.assertIn("dependency-free", loaded.get_json()["memory"])
            self.assertEqual(
                data_dir.joinpath("memory.md").read_text(encoding="utf-8"),
                "- Prefer small, dependency-free changes.\n",
            )

    def test_repository_instructions_are_loaded_and_dependencies_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            workspace.mkdir()
            workspace.joinpath("AGENTS.md").write_text(
                "# Root instructions\nUse the project formatter.", encoding="utf-8"
            )
            nested = workspace / "src"
            nested.mkdir()
            nested.joinpath("CLAUDE.md").write_text(
                "# Source instructions\nKeep imports sorted.", encoding="utf-8"
            )
            ignored = workspace / "node_modules"
            ignored.mkdir()
            ignored.joinpath("AGENTS.md").write_text(
                "This must not be loaded.", encoding="utf-8"
            )

            prompt = build_project_context(workspace, Path(root) / "data")

            self.assertIn("AGENTS.md", prompt)
            self.assertIn("Use the project formatter.", prompt)
            self.assertIn("src/CLAUDE.md", prompt)
            self.assertIn("Keep imports sorted.", prompt)
            self.assertNotIn("This must not be loaded.", prompt)

    def test_mcp_stdio_client(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            config_dir = workspace / ".codelite"
            config_dir.mkdir()
            config = config_dir / "mcp.json"
            existing = {"mcpServers": {"inactive": {"disabled": True}}, "metadata": 1}
            config.write_text(json.dumps(existing), encoding="utf-8")
            context = self.context(workspace, Path(root) / "data")
            add = {
                "action": "add_mcp_server", "server": "fixture", "command": sys.executable,
                "args": [str(FIXTURES / "fake_mcp_server.py")],
            }
            with patch.object(context.permissions, "require_write", side_effect=PermissionDenied("denied")):
                with self.assertRaises(PermissionDenied):
                    EXTENSIONS.run(add, context)
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), existing)

            concurrent = {**existing, "metadata": 2}
            with patch.object(context.permissions, "require_write", side_effect=lambda *_: config.write_text(
                json.dumps(concurrent), encoding="utf-8",
            )):
                with self.assertRaisesRegex(ToolError, "changed while adding"):
                    EXTENSIONS.run(add, context)
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), concurrent)

            with patch.object(context.permissions, "require_shell", side_effect=PermissionDenied("denied")):
                self.assertTrue(json.loads(EXTENSIONS.run(add, context))["saved"])
                with self.assertRaises(PermissionDenied):
                    EXTENSIONS.run({"action": "list_mcp_tools", "server": "fixture"}, context)
            with self.assertRaisesRegex(ToolError, "already exists"):
                EXTENSIONS.run({**add, "server": "inactive"}, context)
            saved = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(saved["metadata"], 2)
            self.assertEqual(saved["mcpServers"]["inactive"], {"disabled": True})
            tools = json.loads(
                EXTENSIONS.run(
                    {"action": "list_mcp_tools", "server": "fixture"}, context
                )
            )
            self.assertEqual(tools[0]["name"], "echo")
            result = json.loads(
                EXTENSIONS.run(
                    {
                        "action": "call_mcp_tool",
                        "server": "fixture",
                        "name": "echo",
                        "arguments": {"value": 7},
                    },
                    context,
                )
            )
            self.assertIn('"value": 7', result["content"][0]["text"])

    def test_lsp_code_intelligence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "workspace"
            data_dir = Path(root) / "data"
            config_dir = workspace / ".codelite"
            config_dir.mkdir(parents=True)
            config_dir.joinpath("lsp.json").write_text(
                json.dumps(
                    {
                        "servers": {
                            "fixture": {
                                "command": sys.executable,
                                "args": [str(FIXTURES / "fake_lsp_server.py")],
                                "extensions": [".fixture"],
                                "languageId": "fixture",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            workspace.joinpath("sample.fixture").write_text("example\n", encoding="utf-8")
            context = self.context(workspace, data_dir)
            diagnostics = json.loads(
                CODE_INTELLIGENCE.run(
                    {"action": "diagnostics", "path": "sample.fixture"}, context
                )
            )
            self.assertEqual(diagnostics["results"][0]["message"], "Fixture warning")
            definition = json.loads(
                CODE_INTELLIGENCE.run(
                    {
                        "action": "definition",
                        "path": "sample.fixture",
                        "line": 1,
                        "character": 2,
                    },
                    context,
                )
            )
            self.assertEqual(definition["results"][0]["range"]["start"]["line"], 1)


if __name__ == "__main__":
    unittest.main()
