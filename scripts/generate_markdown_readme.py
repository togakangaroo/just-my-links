# /// script
# requires-python = ">=3.11"
# ///
"""
Pre-commit hook: keeps README.md in sync with README.ipynb.

- If README.ipynb is staged: regenerate README.md, stage it, and fail so the
  user can review and re-commit with the fresh output.
- If README.md is staged without README.ipynb: reject — it's auto-generated.
"""
import subprocess
import sys
from pathlib import Path

HEADER = "<!-- This file is auto-generated from README.ipynb. DO NOT MODIFY IT DIRECTLY. -->"


def staged_files() -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True,
    )
    return set(result.stdout.splitlines())


def is_tracked(path: str) -> bool:
    return subprocess.run(
        ["git", "ls-files", "--error-unmatch", path],
        capture_output=True,
    ).returncode == 0


def has_unstaged_changes(path: str) -> bool:
    return subprocess.run(
        ["git", "diff", "--quiet", path],
        capture_output=True,
    ).returncode != 0


def main() -> int:
    staged = staged_files()

    if "README.ipynb" in staged:
        subprocess.run(
            [
                "uv", "run", "jupyter", "nbconvert",
                "--to", "markdown",
                '--TagRemovePreprocessor.remove_cell_tags=["omit_from_markdown_export"]',
                "README.ipynb",
            ],
            check=True,
        )

        readme = Path("README.md")
        readme.write_text(f"{HEADER}\n{readme.read_text()}")

        if not is_tracked("README.md") or has_unstaged_changes("README.md"):
            subprocess.run(["git", "add", "README.md"], check=True)
            print("README.md is out of sync. It has been regenerated. Please try again.")
            return 1

    elif "README.md" in staged:
        print("README.md is auto-generated. Edit README.ipynb instead.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
