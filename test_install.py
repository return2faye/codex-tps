import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("installer", Path(__file__).with_name("install.py"))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def test_refuses_unowned_command_before_plugin_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            target = home / ".local/bin/codex-tps"
            target.parent.mkdir(parents=True)
            target.write_text("user command\n")
            calls = []

            with self.assertRaisesRegex(RuntimeError, "Refusing to replace"):
                installer.install(repo=temp, home=home, which=lambda name: name,
                                  run=lambda *args, **kwargs: calls.append(args))

            self.assertEqual(calls, [])
            self.assertEqual(target.read_text(), "user command\n")

    def test_refuses_symlink_before_plugin_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            target = home / ".local/bin/codex-tps"
            target.parent.mkdir(parents=True)
            target.symlink_to(home / "missing")
            calls = []

            with self.assertRaisesRegex(RuntimeError, "existing symlink"):
                installer.install(repo=temp, home=home, which=lambda name: name,
                                  run=lambda *args, **kwargs: calls.append(args))

            self.assertEqual(calls, [])
            self.assertTrue(target.is_symlink())

    def test_wrapper_quotes_installed_launcher_path(self):
        launcher = "/tmp/plugin cache/bin/codex-tps"
        wrapper = installer.wrapper_text(launcher)
        self.assertIn(installer.MARKER, wrapper)
        self.assertIn("exec '/tmp/plugin cache/bin/codex-tps' \"$@\"", wrapper)

    def test_installs_wrapper_from_cli_reported_cache_path(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            launcher = home / "plugin cache/bin/codex-tps"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\n")
            calls = []

            class Result:
                stdout = '{"installedPath":"' + str(launcher.parent.parent) + '"}'

            def run(args, **kwargs):
                calls.append(args)
                return Result()

            destination = installer.install(repo=temp, home=home, which=lambda name: name, run=run)

            self.assertEqual(destination.read_text(), installer.wrapper_text(launcher))
            self.assertTrue(destination.stat().st_mode & 0o111)
            self.assertEqual(calls[0][-1], str(Path(temp).resolve()))
            self.assertEqual(calls[1][3:], ["codex-tps@codex-tps-dev", "--json"])

    def test_reinstall_updates_owned_wrapper(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            launcher = home / "plugin cache/bin/codex-tps"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/sh\n")
            result = type("Result", (), {
                "stdout": '{"installedPath":"' + str(launcher.parent.parent) + '"}'
            })()
            calls = []

            destination = installer.install(repo=temp, home=home, which=lambda name: name,
                                             run=lambda args, **kwargs: calls.append(args) or result)
            installer.install(repo=temp, home=home, which=lambda name: name,
                              run=lambda args, **kwargs: calls.append(args) or result)

            self.assertEqual(destination.read_text(), installer.wrapper_text(launcher))
            self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
