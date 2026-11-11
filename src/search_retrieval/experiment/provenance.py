"""Checks that bind a run to its repository, code commit, runtime and split."""

from hashlib import file_digest
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import platform
import re
import subprocess
import sysconfig

from search_retrieval.experiment.manifests import inside
from search_retrieval.experiment.run_config import ResolvedRun


class ProvenanceError(RuntimeError):
    """The run cannot be tied to the declared code, runtime or split policy."""


# After the code commit, only experiment declarations, documentation and archived evidence may change.
DECLARATION_PATHS = ("config/runs/", "manifests/", "docs/", "engineering/", "evaluation/evidence/")
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except OSError as error:
        raise ProvenanceError("Git is unavailable") from error
    if result.returncode:
        raise ProvenanceError(f"git {' '.join(arguments)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def verify_repository(root: Path) -> None:
    """The project must be the top of its own work tree, not a directory inside another repository."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if not os.path.samefile(top, root):
        raise ProvenanceError(f"{root} is not the top level of its Git repository ({top})")


def verify_clean(root: Path) -> None:
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ProvenanceError("The work tree has uncommitted or untracked files")


def verify_code_commit(root: Path, commit: str) -> None:
    """HEAD may differ from the code commit only by experiment declarations."""
    if _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}") != commit:
        raise ProvenanceError("code_commit is not a full commit ID of this repository")
    try:
        _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
    except ProvenanceError as error:
        raise ProvenanceError("code_commit is not an ancestor of HEAD") from error
    changed = [name for name in _git(root, "diff", "--name-only", commit, "HEAD").splitlines()
               if not name.startswith(DECLARATION_PATHS)]
    if changed:
        raise ProvenanceError(f"Code changed after code_commit: {changed[:5]}")


def verify_provenance(root: Path, resolved: ResolvedRun) -> None:
    verify_repository(root)
    verify_clean(root)
    verify_code_commit(root, resolved.run.code_commit)


def authorize_split(root: Path, command: str, resolved: ResolvedRun, frozen_dev: ResolvedRun | None) -> None:
    """Test judgments are read only by `report`, for a configuration frozen in an earlier commit.

    Call after `verify_provenance`: a clean tree makes the working files equal to HEAD.
    """
    if command == "run":
        if resolved.split == "test":
            raise ProvenanceError("Only the report command accepts the test split")
        return
    if command != "report":
        raise ProvenanceError(f"Unknown command: {command}")
    if resolved.split != "test":
        raise ProvenanceError("The report command is reserved for the test split")
    if frozen_dev is None or frozen_dev.split != "dev":
        raise ProvenanceError("A test report requires the frozen development configuration")
    if frozen_dev.frozen_digest != resolved.frozen_digest:
        raise ProvenanceError("The test configuration differs from the frozen development configuration")
    head = _git(root, "rev-parse", "HEAD")
    frozen_at = _git(root, "log", "-1", "--format=%H", "--", frozen_dev.path)
    if not frozen_at:
        raise ProvenanceError("The frozen development configuration is not committed")
    if frozen_at == head:
        raise ProvenanceError("The development configuration must be frozen in a commit before HEAD")


def observed_runtime(root: Path, runtime: dict) -> dict:
    """Compare the interpreter, platform and installed pins with the runtime manifest."""
    tag = re.sub(r"[-.]", "_", sysconfig.get_platform())
    if (platform.python_version(), tag) != (runtime["python"], runtime["platform"]):
        raise ProvenanceError(
            f"Runtime is {platform.python_version()} on {tag}, "
            f"declared {runtime['python']} on {runtime['platform']}"
        )
    packages = {}
    for relative, expected in sorted(runtime["locks"].items()):
        path = inside(root, relative)
        try:
            with path.open("rb") as source:
                actual = file_digest(source, "sha256").hexdigest()
        except OSError as error:
            raise ProvenanceError(f"Missing lock file: {relative}") from error
        if actual != expected:
            raise ProvenanceError(f"Lock file changed: {relative}")
        for line in path.read_text(encoding="utf-8").splitlines():
            if pin := PIN.match(line.strip()):
                name, pinned = pin.groups()
                try:
                    installed = version(name)
                except PackageNotFoundError as error:
                    raise ProvenanceError(f"Locked package is not installed: {name}") from error
                if installed != pinned:
                    raise ProvenanceError(f"{name} is {installed}, locked {pinned}")
                packages[name] = installed
    if not packages:
        raise ProvenanceError("The runtime locks pin no package")
    return {"python": runtime["python"], "platform": runtime["platform"], "packages": packages}
