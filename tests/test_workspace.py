"""Tests del workspace git (copias de trabajo en out/)."""

import subprocess
import unittest

import lib.workspace as ws
from tests.support import TempDirTestCase, requires_git


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@requires_git
class EnsureWorkspaceTests(TempDirTestCase):
    def _make_project(self, name="proj", with_git=False):
        projects = self.tmp / "projects"
        project = projects / name
        (project / "src").mkdir(parents=True)
        (project / "src" / "A.java").write_text("class A {}\n", encoding="utf-8")
        if with_git:
            subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                            "add", "-A"], cwd=project, check=True)
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-m", "init"], cwd=project, check=True,
                           capture_output=True)
        return projects, project

    def test_copies_project_without_git_history(self):
        projects, project = self._make_project()
        work = self.tmp / "out"
        pairs = ws.ensure_workspace(str(projects), str(work))
        self.assertEqual([p[0] for p in pairs], ["proj"])
        work_project = work / "proj"
        self.assertTrue((work_project / ".git").exists())
        self.assertTrue((work_project / "src" / "A.java").exists())
        self.assertTrue(git(work_project, "log", "-1", "--pretty=%s"))

    def test_clones_project_with_git_history(self):
        projects, project = self._make_project(with_git=True)
        work = self.tmp / "out"
        pairs = ws.ensure_workspace(str(projects), str(work))
        self.assertTrue((pairs[0][1] / ".git").exists())

    def test_reuses_existing_workspace(self):
        projects, _ = self._make_project()
        work = self.tmp / "out"
        ws.ensure_workspace(str(projects), str(work))
        pairs = ws.ensure_workspace(str(projects), str(work))
        self.assertEqual(len(pairs), 1)

    def test_only_filter(self):
        self._make_project("a")
        self._make_project("b")
        projects = self.tmp / "projects"
        pairs = ws.ensure_workspace(str(projects), str(self.tmp / "out"), only={"b"})
        self.assertEqual([p[0] for p in pairs], ["b"])

    def test_missing_projects_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            ws.ensure_workspace(str(self.tmp / "nope"), str(self.tmp / "out"))


@requires_git
class GitOpsTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        (self.repo / "A.java").write_text("original\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-m", "init"], cwd=self.repo, check=True,
                       capture_output=True)

    def test_git_restore(self):
        (self.repo / "A.java").write_text("modificado\n", encoding="utf-8")
        ws.git_restore(self.repo, "A.java")
        self.assertEqual((self.repo / "A.java").read_text(encoding="utf-8"),
                         "original\n")

    def test_git_restore_accepts_windows_separator(self):
        nested = self.repo / "src"
        nested.mkdir()
        (nested / "B.java").write_text("b\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t",
                        "-c", "user.email=t@t", "commit", "-m", "b"], check=True,
                       capture_output=True)
        (nested / "B.java").write_text("cambiado\n", encoding="utf-8")
        ws.git_restore(self.repo, "src\\B.java")
        self.assertEqual((nested / "B.java").read_text(encoding="utf-8"), "b\n")

    def test_git_commit(self):
        (self.repo / "A.java").write_text("nuevo\n", encoding="utf-8")
        ws.git_commit(self.repo, "refactor de prueba")
        self.assertEqual(git(self.repo, "log", "-1", "--pretty=%s"), "refactor de prueba")
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
