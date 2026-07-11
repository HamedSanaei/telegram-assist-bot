"""Validate repository text files for UTF-8 and common corruption markers."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

_GIT_TIMEOUT_SECONDS = 30.0
_REPLACEMENT_CHARACTER = chr(0xFFFD)
_BOM_CHARACTER = chr(0xFEFF)
_QUESTION_MARK_RUN = re.compile(r"\?{4,}")
_MOJIBAKE_CODE_POINTS = frozenset(
    {
        0x00C2,
        0x00C3,
        0x00D8,
        0x00D9,
        0x00DA,
        0x00DB,
    }
)
_TEXT_FILE_NAMES = frozenset(
    {
        ".coveragerc",
        ".editorconfig",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        ".gitkeep",
        ".secrets.baseline",
        "dockerfile",
        "license",
        "makefile",
        "py.typed",
        "uv.lock",
    }
)
_TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cfg",
        ".conf",
        ".css",
        ".csv",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".log",
        ".md",
        ".ps1",
        ".py",
        ".pyi",
        ".rst",
        ".service",
        ".sh",
        ".toml",
        ".ts",
        ".tsv",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


class IssueCode(StrEnum):
    """Stable categories reported by the text-integrity scanner."""

    INVALID_UTF8 = "invalid_utf8"
    UTF8_BOM = "utf8_bom"
    REPLACEMENT_CHARACTER = "replacement_character"
    MOJIBAKE = "mojibake"
    QUESTION_MARK_RUN = "question_mark_run"


@dataclass(frozen=True, slots=True)
class TextIssue:
    """Describe one integrity problem without exposing the affected text."""

    path: PurePosixPath
    code: IssueCode
    line: int | None = None
    column: int | None = None
    byte_offset: int | None = None


@dataclass(frozen=True, slots=True)
class AllowRule:
    """Permit a bounded occurrence of one documented intentional finding."""

    path: PurePosixPath
    code: IssueCode
    exact_line: str
    maximum_occurrences: int
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Contain the deterministic result of scanning selected repository paths."""

    files_checked: int
    issues: tuple[TextIssue, ...]


class TextIntegrityToolError(RuntimeError):
    """Signal an operational failure that prevents a trustworthy scan."""


GitRunner = Callable[[Path, tuple[str, ...]], bytes]


def _characters(*code_points: int) -> str:
    return "".join(chr(code_point) for code_point in code_points)


_EXAMPLE_ONE = _characters(
    0x00D8, 0x00B3, 0x00D9, 0x0084, 0x00D8, 0x00A7, 0x00D9, 0x0085
)
_EXAMPLE_TWO = _characters(0x00D9, 0x0085, 0x00D8, 0x00AA, 0x00D9, 0x0086)
_EXAMPLE_THREE = _characters(
    0x00DA,
    0x00A9,
    0x00D8,
    0x00A7,
    0x00D8,
    0x00B1,
    0x00D8,
    0x00A8,
    0x00D8,
    0x00B1,
)
_MARKER_GUIDANCE_LINE = (
    "   `"
    + chr(0x00D8)
    + "`, `"
    + chr(0x00D9)
    + "`, `"
    + chr(0x00DB)
    + "`, `"
    + chr(0x00C3)
    + "`, `"
    + chr(0x00C2)
    + "`, `"
    + _REPLACEMENT_CHARACTER
    + "`, and unexpected `"
    + "?" * 4
    + "`."
)

DOCUMENTATION_ALLOWLIST: tuple[AllowRule, ...] = (
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.MOJIBAKE,
        exact_line=_EXAMPLE_ONE,
        maximum_occurrences=1,
        reason="Intentional corrupted-text example in the encoding policy.",
    ),
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.MOJIBAKE,
        exact_line=_EXAMPLE_TWO,
        maximum_occurrences=1,
        reason="Intentional corrupted-text example in the encoding policy.",
    ),
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.MOJIBAKE,
        exact_line=_EXAMPLE_THREE,
        maximum_occurrences=1,
        reason="Intentional corrupted-text example in the encoding policy.",
    ),
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.MOJIBAKE,
        exact_line=_MARKER_GUIDANCE_LINE,
        maximum_occurrences=1,
        reason="Documented list of markers that reviewers must search for.",
    ),
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.REPLACEMENT_CHARACTER,
        exact_line=_MARKER_GUIDANCE_LINE,
        maximum_occurrences=1,
        reason="Documented replacement-character marker for reviewers.",
    ),
    AllowRule(
        path=PurePosixPath("AGENTS.md"),
        code=IssueCode.QUESTION_MARK_RUN,
        exact_line=_MARKER_GUIDANCE_LINE,
        maximum_occurrences=1,
        reason="Documented unexpected-question-mark example for reviewers.",
    ),
)

_ISSUE_MESSAGES: dict[IssueCode, str] = {
    IssueCode.INVALID_UTF8: "file is not valid strict UTF-8",
    IssueCode.UTF8_BOM: "unexpected UTF-8 BOM character",
    IssueCode.REPLACEMENT_CHARACTER: "Unicode replacement character detected",
    IssueCode.MOJIBAKE: "common mojibake marker detected",
    IssueCode.QUESTION_MARK_RUN: "unexpected run of ASCII question marks detected",
}


def is_text_path(path: PurePosixPath) -> bool:
    """Return whether a repository path is governed by the text policy."""
    lowered_name = path.name.lower()
    return lowered_name in _TEXT_FILE_NAMES or path.suffix.lower() in _TEXT_SUFFIXES


def parse_nul_paths(output: bytes) -> tuple[PurePosixPath, ...]:
    """Parse unquoted, NUL-delimited Git paths and reject unsafe entries."""
    paths: list[PurePosixPath] = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        try:
            decoded_path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise TextIntegrityToolError(
                "Git returned a path that is not valid UTF-8."
            ) from error

        path = PurePosixPath(decoded_path)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise TextIntegrityToolError(
                "Git returned an unsafe repository-relative path."
            )
        if path.parts[0] == ".git":
            raise TextIntegrityToolError(
                "Refusing to inspect Git metadata as repository text."
            )
        paths.append(path)

    return tuple(paths)


def _run_git(repo_root: Path, arguments: tuple[str, ...]) -> bytes:
    git_executable = shutil.which("git")
    if git_executable is None:
        raise TextIntegrityToolError("Git executable was not found.")

    try:
        # The executable is resolved explicitly, no shell is used, and callers
        # pass fixed arguments.
        completed: subprocess.CompletedProcess[bytes] = subprocess.run(  # noqa: S603
            (git_executable, *arguments),
            cwd=repo_root,
            check=False,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise TextIntegrityToolError(
            "Git path discovery exceeded its bounded timeout."
        ) from error
    except OSError as error:
        raise TextIntegrityToolError(
            "Git path discovery could not be executed."
        ) from error

    if completed.returncode != 0:
        raise TextIntegrityToolError(
            f"Git path discovery failed with exit code {completed.returncode}."
        )
    return completed.stdout


def _collect_paths(
    repo_root: Path,
    commands: Sequence[tuple[str, ...]],
    runner: GitRunner,
) -> tuple[PurePosixPath, ...]:
    discovered: set[PurePosixPath] = set()
    for command in commands:
        discovered.update(parse_nul_paths(runner(repo_root, command)))
    return tuple(sorted(discovered, key=lambda path: path.as_posix()))


def collect_changed_paths(
    repo_root: Path,
    *,
    runner: GitRunner = _run_git,
) -> tuple[PurePosixPath, ...]:
    """Collect staged, unstaged, and non-ignored untracked repository paths."""
    commands = (
        ("diff", "--name-only", "--diff-filter=ACMRTUXB", "-z", "--"),
        ("diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    )
    return _collect_paths(repo_root, commands, runner)


def collect_all_paths(
    repo_root: Path,
    *,
    runner: GitRunner = _run_git,
) -> tuple[PurePosixPath, ...]:
    """Collect every tracked or non-ignored untracked repository path."""
    commands = (("ls-files", "--cached", "--others", "--exclude-standard", "-z", "--"),)
    return _collect_paths(repo_root, commands, runner)


def _first_mojibake_column(line: str) -> int | None:
    for index, character in enumerate(line):
        code_point = ord(character)
        if code_point in _MOJIBAKE_CODE_POINTS or 0x0080 <= code_point <= 0x009F:
            return index + 1
    return None


def _record_issue(
    issue: TextIssue,
    line: str,
    allowlist: Sequence[AllowRule],
    allowance_usage: dict[AllowRule, int],
    issues: list[TextIssue],
) -> None:
    for rule in allowlist:
        if (
            rule.path != issue.path
            or rule.code != issue.code
            or rule.exact_line != line
        ):
            continue
        used = allowance_usage.get(rule, 0)
        if used < rule.maximum_occurrences:
            allowance_usage[rule] = used + 1
            return
    issues.append(issue)


def scan_text(
    path: PurePosixPath,
    text: str,
    *,
    allowlist: Sequence[AllowRule] = DOCUMENTATION_ALLOWLIST,
) -> tuple[TextIssue, ...]:
    """Inspect already-decoded text without modifying or normalizing it."""
    issues: list[TextIssue] = []
    allowance_usage: dict[AllowRule, int] = {}

    # Split only on physical LF. Some intentional mojibake samples contain C1
    # characters that str.splitlines() would incorrectly interpret as line breaks.
    for line_number, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.removesuffix("\r")

        bom_column = line.find(_BOM_CHARACTER)
        if bom_column >= 0:
            _record_issue(
                TextIssue(
                    path=path,
                    code=IssueCode.UTF8_BOM,
                    line=line_number,
                    column=bom_column + 1,
                ),
                line,
                allowlist,
                allowance_usage,
                issues,
            )

        replacement_column = line.find(_REPLACEMENT_CHARACTER)
        if replacement_column >= 0:
            _record_issue(
                TextIssue(
                    path=path,
                    code=IssueCode.REPLACEMENT_CHARACTER,
                    line=line_number,
                    column=replacement_column + 1,
                ),
                line,
                allowlist,
                allowance_usage,
                issues,
            )

        mojibake_column = _first_mojibake_column(line)
        if mojibake_column is not None:
            _record_issue(
                TextIssue(
                    path=path,
                    code=IssueCode.MOJIBAKE,
                    line=line_number,
                    column=mojibake_column,
                ),
                line,
                allowlist,
                allowance_usage,
                issues,
            )

        question_mark_match = _QUESTION_MARK_RUN.search(line)
        if question_mark_match is not None:
            _record_issue(
                TextIssue(
                    path=path,
                    code=IssueCode.QUESTION_MARK_RUN,
                    line=line_number,
                    column=question_mark_match.start() + 1,
                ),
                line,
                allowlist,
                allowance_usage,
                issues,
            )

    return tuple(issues)


def _resolve_repo_file(repo_root: Path, relative_path: PurePosixPath) -> Path:
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or not relative_path.parts
    ):
        raise TextIntegrityToolError(
            "Refusing to inspect an unsafe repository-relative path."
        )

    try:
        resolved_root = repo_root.resolve(strict=True)
        candidate = resolved_root.joinpath(*relative_path.parts)
        if candidate.is_symlink():
            raise TextIntegrityToolError(
                "Refusing to follow a repository text-file symlink."
            )
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise TextIntegrityToolError(
            f"Repository text path is unavailable: {relative_path.as_posix()}"
        ) from error

    if (
        not resolved_candidate.is_relative_to(resolved_root)
        or not resolved_candidate.is_file()
    ):
        message = "Repository text path is not a safe regular file: "
        raise TextIntegrityToolError(message + relative_path.as_posix())
    return resolved_candidate


def scan_file(
    repo_root: Path,
    relative_path: PurePosixPath,
    *,
    allowlist: Sequence[AllowRule] = DOCUMENTATION_ALLOWLIST,
) -> tuple[TextIssue, ...]:
    """Decode one repository file as strict UTF-8 and inspect its text."""
    resolved_path = _resolve_repo_file(repo_root, relative_path)
    try:
        with resolved_path.open(
            "r",
            encoding="utf-8",
            errors="strict",
            newline="",
        ) as text_file:
            text = text_file.read()
    except UnicodeDecodeError as error:
        return (
            TextIssue(
                path=relative_path,
                code=IssueCode.INVALID_UTF8,
                byte_offset=error.start,
            ),
        )
    except OSError as error:
        raise TextIntegrityToolError(
            f"Repository text file could not be read: {relative_path.as_posix()}"
        ) from error

    return scan_text(relative_path, text, allowlist=allowlist)


def scan_paths(
    repo_root: Path,
    paths: Sequence[PurePosixPath],
    *,
    allowlist: Sequence[AllowRule] = DOCUMENTATION_ALLOWLIST,
) -> ScanResult:
    """Scan selected text paths in deterministic order and summarize findings."""
    issues: list[TextIssue] = []
    files_checked = 0
    unique_paths = sorted(set(paths), key=lambda path: path.as_posix())
    for path in unique_paths:
        if not is_text_path(path):
            continue
        files_checked += 1
        issues.extend(scan_file(repo_root, path, allowlist=allowlist))
    return ScanResult(files_checked=files_checked, issues=tuple(issues))


def _format_issue(issue: TextIssue) -> str:
    location = issue.path.as_posix()
    if issue.line is not None:
        location += f":{issue.line}"
        if issue.column is not None:
            location += f":{issue.column}"
    elif issue.byte_offset is not None:
        location += f":byte-{issue.byte_offset}"
    return f"{location}: {issue.code.value}: {_ISSUE_MESSAGES[issue.code]}"


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository text files for UTF-8 and corruption problems."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--changed",
        action="store_true",
        help="scan staged, unstaged, and non-ignored untracked paths",
    )
    modes.add_argument(
        "--all",
        action="store_true",
        dest="scan_all",
        help="scan every tracked or non-ignored untracked path (the default)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line scanner and return its stable process exit code."""
    arguments = _argument_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        paths = (
            collect_changed_paths(repo_root)
            if arguments.changed
            else collect_all_paths(repo_root)
        )
        result = scan_paths(repo_root, paths)
    except TextIntegrityToolError as error:
        print(f"text-integrity tool error: {error}", file=sys.stderr)
        return 2

    for issue in result.issues:
        print(_format_issue(issue), file=sys.stderr)

    if result.issues:
        print(
            f"Text integrity failed: {len(result.issues)} issue(s) "
            f"in {result.files_checked} checked file(s).",
            file=sys.stderr,
        )
        return 1

    print(f"Text integrity passed for {result.files_checked} checked file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
