from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "annual-pipeline.yml"


class AnnualPipelineWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_triggers_permissions_and_pipeline_command(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn('cron: "17 8 * 4-6 4"', self.workflow)
        self.assertIn("contents: write", self.workflow)
        self.assertIn("pull-requests: write", self.workflow)
        self.assertIn("PYTHON=python scripts/run_annual_pipeline.sh", self.workflow)

    def test_changed_path_allowlist_is_enforced_before_commit(self) -> None:
        for root in [
            "data/raw/michelin/",
            "data/partitions/",
            "data/candidates/insee/",
            "data/products/insee/",
            "data/products/france/",
            "data/reports/",
        ]:
            self.assertIn(f'"{root}"', self.workflow)
        self.assertIn('["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"]', self.workflow)
        self.assertIn('if "R" in code or "C" in code:', self.workflow)
        self.assertIn("Unexpected changed paths detected", self.workflow)
        self.assertIn("refusing to create a generic annual PR", self.workflow)

    def test_git_writes_are_limited_to_deterministic_automation_branch(self) -> None:
        self.assertIn("if: steps.inspect_changes.outputs.has_changes == 'true'", self.workflow)
        self.assertIn("if: steps.annual_commit.outputs.has_commit == 'true'", self.workflow)
        self.assertIn('f"automation/annual-pipeline-{guide_year}"', self.workflow)
        self.assertIn('git commit -m "Add ${year} Michelin annual data products"', self.workflow)
        self.assertIn('git push origin "${candidate_sha}:refs/heads/${branch}"', self.workflow)
        self.assertIn("--force-with-lease=", self.workflow)
        self.assertIn("Existing automation branch contains non-bot commits", self.workflow)
        self.assertNotIn("git add -A", self.workflow)
        self.assertNotIn("git add -- tmp/logs", self.workflow)
        self.assertNotIn("git push origin main", self.workflow)
        self.assertNotIn("git push origin master", self.workflow)

    def test_pr_and_artifact_safety_contracts(self) -> None:
        self.assertIn("gh pr list", self.workflow)
        self.assertIn("gh pr create", self.workflow)
        self.assertLess(self.workflow.index("gh pr list"), self.workflow.index("gh pr create"))
        self.assertIn("Manual review and merge are required.", self.workflow)
        self.assertIn("if: always()", self.workflow)
        self.assertIn("tmp/logs/", self.workflow)
        self.assertNotIn("--auto", self.workflow)
        self.assertNotIn("gh pr merge", self.workflow)
        self.assertNotIn("wine_pipeline", self.workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
