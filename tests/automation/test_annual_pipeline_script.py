from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
import unittest

from tests.support import REPOSITORY_ROOT

REPO_ROOT = REPOSITORY_ROOT
STUB_RELATIVE_PATH = Path("tests/automation/shell/stub_python.sh")

STAGE1 = "-m data_pipeline partition --acquire-next"
INSEE_BUILD_2024 = "-m insee_pipeline build --year 2024"
INSEE_PRODUCT_2024 = "-m insee_pipeline product --year 2024"
STAGE2_FRANCE_2027_INSEE_2024 = "-m data_pipeline departments --year 2027 --insee-year 2024"
STAGE2_FRANCE_2027_INSEE_2023 = "-m data_pipeline departments --year 2027 --insee-year 2023"
STAGE2_MONACO_2027 = "-m data_pipeline monaco --year 2027"
STAGE3_2027 = "-m data_pipeline arrondissements --year 2027"
GUIDE_CHANGES_2026_2027 = "-m data_pipeline changes --previous-year 2026 --current-year 2027"


class AnnualPipelineShellHarnessTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.repo_snapshot = self._repo_snapshot()

    def tearDown(self) -> None:
        self.assertEqual(
            self._repo_snapshot(),
            self.repo_snapshot,
            "The harness changed the real repository working tree.",
        )

    def run_scenario(self, scenario: str) -> tuple[Path, subprocess.CompletedProcess[str], list[str]]:
        sandbox = Path(tempfile.gettempdir()) / f"michelin-annual-script-test-{scenario}"
        if not str(sandbox).startswith(tempfile.gettempdir()):
            raise AssertionError(f"Refusing to remove unexpected sandbox path: {sandbox}")
        shutil.rmtree(sandbox, ignore_errors=True)
        self._copy_worktree(sandbox)
        self._seed_minimal_canonical_state(sandbox)

        stub = sandbox / STUB_RELATIVE_PATH
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        command_log = sandbox / "tmp" / "stub-command.log"
        command_log.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "PYTHON": str(stub),
                "STUB_SCENARIO": scenario,
                "STUB_PROJECT_ROOT": str(sandbox),
                "STUB_COMMAND_LOG": str(command_log),
            }
        )
        env.pop("PYTHONPATH", None)

        result = subprocess.run(
            [str(sandbox / "scripts" / "run_annual_pipeline.sh")],
            cwd=sandbox,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        commands = command_log.read_text(encoding="utf-8").splitlines() if command_log.exists() else []
        if self._test_has_failed(result):
            print(f"Scenario sandbox retained for inspection: {sandbox}")
        return sandbox, result, commands

    def test_no_new_guide_skips_downstream_commands(self) -> None:
        sandbox, result, commands = self.run_scenario("no_new_guide")

        self.assertEqual(result.returncode, 0, self._failure_message(sandbox, result))
        self.assertEqual(commands, [STAGE1])
        self.assertIn("Latest accepted France partition before Stage 1: 2026", result.stdout)
        self.assertIn("Latest accepted France partition after Stage 1: 2026", result.stdout)
        self.assertIn("No new Michelin guide was published", result.stdout)
        self.assertIn("Stage 2 France: skipped", result.stdout)
        self.assertFalse((sandbox / "data/partitions/france/france_2027.csv").exists())
        self.assertFalse((sandbox / "data/products/insee/2024").exists())

    def test_new_guide_insee_success_runs_all_stages_in_order(self) -> None:
        sandbox, result, commands = self.run_scenario("insee_success")

        self.assertEqual(result.returncode, 0, self._failure_message(sandbox, result))
        self.assertEqual(
            commands,
            [
                STAGE1,
                INSEE_BUILD_2024,
                INSEE_PRODUCT_2024,
                STAGE2_FRANCE_2027_INSEE_2024,
                STAGE2_MONACO_2027,
                STAGE3_2027,
                GUIDE_CHANGES_2026_2027,
            ],
        )
        self.assertLess(commands.index(INSEE_PRODUCT_2024), commands.index(STAGE2_FRANCE_2027_INSEE_2024))
        self.assertIn("Attempted INSEE year: 2024", result.stdout)
        self.assertIn("INSEE year used: 2024", result.stdout)
        self.assertIn("INSEE fallback used: no", result.stdout)
        self.assertTrue((sandbox / "data/partitions/france/france_2027.csv").exists())
        self.assertTrue((sandbox / "data/products/insee/2024/france_departments_2024.csv").exists())
        self.assertTrue((sandbox / "data/products/insee/2024/manifest_2024.json").exists())

    def test_new_guide_insee_build_failure_falls_back_to_accepted_year(self) -> None:
        sandbox, result, commands = self.run_scenario("insee_build_failure")

        self.assertEqual(result.returncode, 0, self._failure_message(sandbox, result))
        self.assertEqual(
            commands,
            [
                STAGE1,
                INSEE_BUILD_2024,
                STAGE2_FRANCE_2027_INSEE_2023,
                STAGE2_MONACO_2027,
                STAGE3_2027,
                GUIDE_CHANGES_2026_2027,
            ],
        )
        self.assertNotIn(INSEE_PRODUCT_2024, commands)
        self.assertIn("Attempted INSEE year: 2024", result.stdout)
        self.assertIn("INSEE year used: 2023", result.stdout)
        self.assertIn("INSEE fallback used: yes", result.stdout)
        self.assertTrue((sandbox / "data/products/insee/2023/france_departments_2023.csv").exists())
        self.assertFalse((sandbox / "data/products/insee/2024").exists())

    def test_new_guide_insee_product_failure_falls_back_without_accepting_2024(self) -> None:
        sandbox, result, commands = self.run_scenario("insee_product_failure")

        self.assertEqual(result.returncode, 0, self._failure_message(sandbox, result))
        self.assertEqual(
            commands,
            [
                STAGE1,
                INSEE_BUILD_2024,
                INSEE_PRODUCT_2024,
                STAGE2_FRANCE_2027_INSEE_2023,
                STAGE2_MONACO_2027,
                STAGE3_2027,
                GUIDE_CHANGES_2026_2027,
            ],
        )
        self.assertIn("INSEE year used: 2023", result.stdout)
        self.assertIn("INSEE fallback used: yes", result.stdout)
        self.assertTrue((sandbox / "data/products/insee/2024/france_departments_2024.csv").exists())
        self.assertFalse((sandbox / "data/products/insee/2024/manifest_2024.json").exists())

    def test_fatal_stage2_france_failure_stops_later_stages(self) -> None:
        sandbox, result, commands = self.run_scenario("stage2_france_failure")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            commands,
            [
                STAGE1,
                INSEE_BUILD_2024,
                INSEE_PRODUCT_2024,
                STAGE2_FRANCE_2027_INSEE_2024,
            ],
        )
        self.assertNotIn(STAGE2_MONACO_2027, commands)
        self.assertNotIn(STAGE3_2027, commands)
        self.assertNotIn(GUIDE_CHANGES_2026_2027, commands)

    def test_unexpected_france_year_jump_stops_before_insee(self) -> None:
        sandbox, result, commands = self.run_scenario("unexpected_year_jump")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(commands, [STAGE1])
        self.assertIn(
            "Stage 1 published an unexpected France year jump: before=2026 after=2028 expected=2027",
            result.stdout,
        )
        self.assertFalse((sandbox / "data/products/insee/2024").exists())

    def _copy_worktree(self, destination: Path) -> None:
        ignore = shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            ".DS_Store",
            "tmp",
            "*.pyc",
        )
        shutil.copytree(REPO_ROOT, destination, ignore=ignore, symlinks=True)
        self.assertFalse((destination / ".git").exists())
        self.assertFalse((destination / ".venv").exists())

    def _seed_minimal_canonical_state(self, sandbox: Path) -> None:
        data_root = sandbox / "data"
        if data_root.exists():
            shutil.rmtree(data_root)

        france_dir = data_root / "partitions" / "france"
        france_dir.mkdir(parents=True)
        (france_dir / "france_2026.csv").write_text("name\nfixture\n", encoding="utf-8")

        insee_dir = data_root / "products" / "insee" / "2023"
        insee_dir.mkdir(parents=True)
        (insee_dir / "france_departments_2023.csv").write_text("department_code\n01\n", encoding="utf-8")
        (insee_dir / "manifest_2023.json").write_text("{}\n", encoding="utf-8")

    def _repo_snapshot(self) -> tuple[str, tuple[str, ...]]:
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        canonical_files = tuple(
            sorted(
                str(path.relative_to(REPO_ROOT))
                for root in [REPO_ROOT / "data/partitions/france", REPO_ROOT / "data/products/insee"]
                for path in root.rglob("*")
                if path.is_file()
            )
        )
        return status, canonical_files

    def _failure_message(self, sandbox: Path, result: subprocess.CompletedProcess[str]) -> str:
        return (
            f"Scenario sandbox: {sandbox}\n"
            f"returncode={result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def _test_has_failed(self, result: subprocess.CompletedProcess[str]) -> bool:
        return result.returncode == 99


if __name__ == "__main__":
    unittest.main(verbosity=2)
