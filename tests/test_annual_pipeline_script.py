from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "run_annual_pipeline.sh"


class AnnualPipelineScriptTests(unittest.TestCase):
    def make_project(
        self,
        tmp_path: Path,
        *,
        france_years: tuple[int, ...] = (2025, 2026),
        insee_years: tuple[int, ...] = (2023,),
        stage1_publishes: bool = True,
        insee_attempt_fails: bool = False,
        downstream_failure: str | None = None,
    ) -> tuple[Path, Path, Path, dict[str, str]]:
        project = tmp_path / "project"
        scripts = project / "scripts"
        scripts.mkdir(parents=True)
        script = scripts / "run_annual_pipeline.sh"
        shutil.copy2(SCRIPT_SOURCE, script)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        for year in france_years:
            path = project / "data" / "partitions" / "france" / f"france_{year}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("name\nfixture\n", encoding="utf-8")

        for year in insee_years:
            product = project / "data" / "products" / "insee" / str(year)
            product.mkdir(parents=True, exist_ok=True)
            (product / f"france_departments_{year}.csv").write_text("department_code\n01\n", encoding="utf-8")
            (product / f"manifest_{year}.json").write_text("{}\n", encoding="utf-8")

        stub = tmp_path / "stub_python.py"
        stub.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                from __future__ import annotations

                import os
                from pathlib import Path
                import sys

                project = Path.cwd()
                log = Path(os.environ["STUB_COMMAND_LOG"])
                args = sys.argv[1:]
                command = " ".join(args)
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(command + "\\n")

                if any("wine" in arg.lower() for arg in args):
                    raise SystemExit(99)

                stage1_publishes = os.environ.get("STUB_STAGE1_PUBLISHES") == "1"
                insee_attempt_fails = os.environ.get("STUB_INSEE_ATTEMPT_FAILS") == "1"
                downstream_failure = os.environ.get("STUB_DOWNSTREAM_FAILURE", "")

                if args == ["-m", "data_pipeline", "partition", "--acquire-next"]:
                    if stage1_publishes:
                        france_dir = project / "data" / "partitions" / "france"
                        years = sorted(
                            int(path.stem.removeprefix("france_"))
                            for path in france_dir.glob("france_*.csv")
                        )
                        next_year = years[-1] + 1
                        (france_dir / f"france_{next_year}.csv").write_text("name\\nfixture\\n", encoding="utf-8")
                    raise SystemExit(0)

                if args[:3] == ["-m", "insee_pipeline", "build"]:
                    year = args[args.index("--year") + 1]
                    if insee_attempt_fails:
                        incomplete = project / "data" / "products" / "insee" / year
                        incomplete.mkdir(parents=True, exist_ok=True)
                        (incomplete / f"france_departments_{year}.csv").write_text("incomplete\\n", encoding="utf-8")
                        raise SystemExit(2)
                    candidate = project / "data" / "candidates" / "insee" / year
                    candidate.mkdir(parents=True, exist_ok=True)
                    raise SystemExit(0)

                if args[:3] == ["-m", "insee_pipeline", "product"]:
                    year = args[args.index("--year") + 1]
                    product = project / "data" / "products" / "insee" / year
                    product.mkdir(parents=True, exist_ok=True)
                    (product / f"france_departments_{year}.csv").write_text("department_code\\n01\\n", encoding="utf-8")
                    (product / f"manifest_{year}.json").write_text("{}\\n", encoding="utf-8")
                    raise SystemExit(0)

                if args[:3] == ["-m", "data_pipeline", "departments"]:
                    if downstream_failure == "departments":
                        raise SystemExit(2)
                    raise SystemExit(0)

                if args[:3] == ["-m", "data_pipeline", "monaco"]:
                    if downstream_failure == "monaco":
                        raise SystemExit(2)
                    raise SystemExit(0)

                if args[:3] == ["-m", "data_pipeline", "arrondissements"]:
                    if downstream_failure == "arrondissements":
                        raise SystemExit(2)
                    raise SystemExit(0)

                if args[:3] == ["-m", "data_pipeline", "changes"]:
                    if downstream_failure == "changes":
                        raise SystemExit(2)
                    raise SystemExit(0)

                raise SystemExit(f"Unexpected command: {command}")
                """
            ),
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        command_log = tmp_path / "commands.log"
        env = {
            "STUB_COMMAND_LOG": str(command_log),
            "STUB_STAGE1_PUBLISHES": "1" if stage1_publishes else "0",
            "STUB_INSEE_ATTEMPT_FAILS": "1" if insee_attempt_fails else "0",
            "STUB_DOWNSTREAM_FAILURE": downstream_failure or "",
        }
        return project, stub, command_log, env

    def run_script(self, project: Path, stub: Path, command_log: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        run_env.update(env)
        run_env["PYTHON"] = str(stub)
        return subprocess.run(
            [str(project / "scripts" / "run_annual_pipeline.sh")],
            cwd=project,
            env=run_env,
            text=True,
            capture_output=True,
            check=False,
        )

    def read_commands(self, command_log: Path) -> list[str]:
        if not command_log.exists():
            return []
        return command_log.read_text(encoding="utf-8").splitlines()

    def test_no_downstream_commands_run_when_stage1_publishes_no_new_year(self) -> None:
        with tempfile_project() as tmp_path:
            project, stub, command_log, env = self.make_project(
                tmp_path,
                france_years=(2024, 2026, 2025),
                stage1_publishes=False,
            )
            result = self.run_script(project, stub, command_log, env)
            commands = self.read_commands(command_log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Latest accepted France partition before Stage 1: 2026", result.stdout)
        self.assertIn("No new Michelin guide was published", result.stdout)
        self.assertEqual(commands, ["-m data_pipeline partition --acquire-next"])

    def test_insee_failure_falls_back_and_downstream_order_is_correct(self) -> None:
        with tempfile_project() as tmp_path:
            project, stub, command_log, env = self.make_project(tmp_path, insee_attempt_fails=True)
            result = self.run_script(project, stub, command_log, env)
            commands = self.read_commands(command_log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Attempted INSEE year: 2024", result.stdout)
        self.assertIn("INSEE year used: 2023", result.stdout)
        self.assertIn("INSEE fallback used: yes", result.stdout)
        self.assertEqual(
            commands,
            [
                "-m data_pipeline partition --acquire-next",
                "-m insee_pipeline build --year 2024",
                "-m data_pipeline departments --year 2027 --insee-year 2023",
                "-m data_pipeline monaco --year 2027",
                "-m data_pipeline arrondissements --year 2027",
                "-m data_pipeline changes --previous-year 2026 --current-year 2027",
            ],
        )
        self.assertFalse(any("wine" in command.lower() for command in commands))

    def test_successful_insee_attempt_uses_new_year(self) -> None:
        with tempfile_project() as tmp_path:
            project, stub, command_log, env = self.make_project(tmp_path)
            result = self.run_script(project, stub, command_log, env)
            commands = self.read_commands(command_log)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Attempted INSEE year: 2024", result.stdout)
        self.assertIn("INSEE year used: 2024", result.stdout)
        self.assertIn("INSEE fallback used: no", result.stdout)
        self.assertIn("-m insee_pipeline build --year 2024", commands)
        self.assertIn("-m insee_pipeline product --year 2024", commands)
        self.assertIn("-m data_pipeline departments --year 2027 --insee-year 2024", commands)

    def test_fatal_downstream_failure_stops_execution(self) -> None:
        with tempfile_project() as tmp_path:
            project, stub, command_log, env = self.make_project(tmp_path, downstream_failure="monaco")
            result = self.run_script(project, stub, command_log, env)
            commands = self.read_commands(command_log)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            commands,
            [
                "-m data_pipeline partition --acquire-next",
                "-m insee_pipeline build --year 2024",
                "-m insee_pipeline product --year 2024",
                "-m data_pipeline departments --year 2027 --insee-year 2024",
                "-m data_pipeline monaco --year 2027",
            ],
        )
        self.assertNotIn("-m data_pipeline arrondissements --year 2027", commands)
        self.assertNotIn("-m data_pipeline changes --previous-year 2026 --current-year 2027", commands)


class tempfile_project:
    def __enter__(self) -> Path:
        import tempfile

        self._temporary_directory = tempfile.TemporaryDirectory()
        return Path(self._temporary_directory.name)

    def __exit__(self, exc_type, exc, tb) -> None:
        self._temporary_directory.cleanup()


if __name__ == "__main__":
    unittest.main()
