from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest

from wine_pipeline.aoc_simplification.assembly import (
    _default_candidate_id,
    _prepare_candidate_dir,
    resolve_simplification_run_id,
)
from wine_pipeline.aoc_simplification.runner import resolve_stage1_input
from wine_pipeline.validation import WinePipelineError


def write_stage1_candidate(root: Path, run_id: str) -> Path:
    path = root / "tmp" / "wine" / run_id / "candidates" / "aoc_regions.gpkg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path.resolve()


def write_batch(root: Path, run_id: str, *, passed: bool = True, complete: bool = True) -> Path:
    run_dir = root / "tmp" / "wine" / "simplification" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"batch_run_id": run_id}), encoding="utf-8")
    if complete:
        (run_dir / "batch_summary.json").write_text(json.dumps({"passed": passed}), encoding="utf-8")
        (run_dir / "validation.json").write_text(json.dumps({"passed": passed}), encoding="utf-8")
        (run_dir / "regions").mkdir()
    return run_dir


class WineCliResolutionTests(unittest.TestCase):
    def test_resolves_exactly_one_stage1_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = write_stage1_candidate(root, "stage1")
            self.assertEqual(resolve_stage1_input(None, project_root=root), expected)

    def test_zero_stage1_candidates_explains_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, r"wine_pipeline build.*--input"):
                resolve_stage1_input(None, project_root=Path(tmp))

    def test_multiple_stage1_candidates_require_explicit_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = write_stage1_candidate(root, "first")
            second = write_stage1_candidate(root, "second")
            with self.assertRaisesRegex(WinePipelineError, r"(?s)Multiple Stage 1.*first.*second.*--input"):
                resolve_stage1_input(None, project_root=root)
            self.assertEqual(resolve_stage1_input(first, project_root=root), first)

    def test_stage1_resolution_ignores_working_and_hidden_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = write_stage1_candidate(root, "real-run")
            for ignored in ("simplification", "diagnostics", "fixture-data", "smoke-test", ".batch.tmp-123"):
                write_stage1_candidate(root, ignored)
            self.assertEqual(resolve_stage1_input(None, project_root=root), expected)

    def test_resolves_exactly_one_validated_simplification_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_batch(root, "validated")
            self.assertEqual(resolve_simplification_run_id(None, project_root=root), "validated")

    def test_incomplete_and_failed_simplification_runs_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_batch(root, "incomplete", complete=False)
            write_batch(root, "failed", passed=False)
            write_batch(root, ".validated.tmp-123")
            write_batch(root, "validated")
            self.assertEqual(resolve_simplification_run_id(None, project_root=root), "validated")

    def test_zero_validated_runs_explains_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_batch(root, "failed", passed=False)
            with self.assertRaisesRegex(FileNotFoundError, r"wine_pipeline simplify.*--simplification-run-id"):
                resolve_simplification_run_id(None, project_root=root)

    def test_multiple_validated_runs_require_explicit_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_batch(root, "alpha")
            write_batch(root, "beta")
            with self.assertRaisesRegex(WinePipelineError, r"(?s)Multiple validated.*alpha.*beta.*--simplification-run-id"):
                resolve_simplification_run_id(None, project_root=root)
            self.assertEqual(resolve_simplification_run_id("beta", project_root=root), "beta")

    def test_explicit_stage1_input_overrides_automatic_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_stage1_candidate(root, "one")
            write_stage1_candidate(root, "two")
            explicit = root / "historical" / "aoc_regions.gpkg"
            explicit.parent.mkdir()
            explicit.write_bytes(b"fixture")
            self.assertEqual(resolve_stage1_input(explicit, project_root=root), explicit.resolve())

    def test_generated_candidate_id_is_safe_and_collision_is_refused(self) -> None:
        candidate_id = _default_candidate_id("Close 500 / Simplify 150")
        self.assertRegex(candidate_id, r"^[a-z0-9_]+$")
        self.assertIn("close_500_simplify_150", candidate_id)
        self.assertTrue(re.search(r"\d{8}t\d{6}", candidate_id))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / candidate_id
            existing.mkdir()
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                _prepare_candidate_dir(root, candidate_id, overwrite=False)


if __name__ == "__main__":
    unittest.main()
