"""Unit tests for the repository text-integrity tooling."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from scripts.check_text_integrity import (
    DOCUMENTATION_ALLOWLIST,
    IssueCode,
    TextIntegrityToolError,
    collect_all_paths,
    collect_changed_paths,
    parse_nul_paths,
    scan_file,
    scan_paths,
    scan_text,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "persian_utf8.json"


def _write_text(root: Path, name: str, content: str) -> PurePosixPath:
    relative_path = PurePosixPath(name)
    target = root.joinpath(*relative_path.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return relative_path


def test_accepts_healthy_persian_utf8_file(tmp_path: Path) -> None:
    """A valid Persian file preserves its exact text and passes inspection."""

    content = "سلام، دنیا!\nنیم\u200cفاصله و ایموجی 😀✨"
    relative_path = _write_text(tmp_path, "healthy.txt", content)

    assert scan_file(tmp_path, relative_path) == ()
    assert (tmp_path / "healthy.txt").read_text(encoding="utf-8") == content


def test_rejects_invalid_utf8_bytes(tmp_path: Path) -> None:
    """A strict decode error is reported as invalid UTF-8."""

    relative_path = PurePosixPath("invalid.txt")
    (tmp_path / "invalid.txt").write_bytes(bytes((0xFF, 0xFE, 0xFA)))

    issues = scan_file(tmp_path, relative_path)

    assert tuple(issue.code for issue in issues) == (IssueCode.INVALID_UTF8,)
    assert issues[0].byte_offset == 0


def test_rejects_utf8_bom(tmp_path: Path) -> None:
    """A valid UTF-8 file with an unexpected BOM is rejected."""

    relative_path = _write_text(tmp_path, "bom.txt", chr(0xFEFF) + "سلام")

    issues = scan_file(tmp_path, relative_path)

    assert IssueCode.UTF8_BOM in {issue.code for issue in issues}


def test_rejects_replacement_character(tmp_path: Path) -> None:
    """An encoded Unicode replacement character is not silently accepted."""

    relative_path = _write_text(tmp_path, "replacement.txt", "متن " + chr(0xFFFD))

    issues = scan_file(tmp_path, relative_path)

    assert IssueCode.REPLACEMENT_CHARACTER in {issue.code for issue in issues}


@pytest.mark.parametrize(
    "code_point",
    [0x00C2, 0x00C3, 0x00D8, 0x00D9, 0x00DA, 0x00DB, 0x0085],
)
def test_rejects_common_mojibake_markers(tmp_path: Path, code_point: int) -> None:
    """Each configured mojibake family is rejected without literals in source."""

    relative_path = _write_text(tmp_path, "mojibake.txt", "broken: " + chr(code_point))

    issues = scan_file(tmp_path, relative_path)

    assert IssueCode.MOJIBAKE in {issue.code for issue in issues}


def test_rejects_four_ascii_question_marks_but_not_three(tmp_path: Path) -> None:
    """Only an unexpected ASCII run at the documented threshold is rejected."""

    short_path = _write_text(tmp_path, "short.txt", "?" * 3)
    long_path = _write_text(tmp_path, "long.txt", "?" * 4)

    assert scan_file(tmp_path, short_path) == ()
    assert IssueCode.QUESTION_MARK_RUN in {
        issue.code for issue in scan_file(tmp_path, long_path)
    }


def test_does_not_treat_persian_question_marks_as_ascii_corruption(
    tmp_path: Path,
) -> None:
    """Persian punctuation remains distinct from replacement question marks."""

    relative_path = _write_text(tmp_path, "persian-question.txt", "؟؟؟؟")

    assert scan_file(tmp_path, relative_path) == ()


def test_documented_agents_examples_are_the_only_allowlisted_lines() -> None:
    """The exact intentional documentation examples pass only at their path."""

    guidance_rule = next(
        rule
        for rule in DOCUMENTATION_ALLOWLIST
        if rule.code is IssueCode.REPLACEMENT_CHARACTER
    )

    assert scan_text(guidance_rule.path, guidance_rule.exact_line) == ()
    copied_issues = scan_text(
        PurePosixPath("docs/copied-example.md"), guidance_rule.exact_line
    )
    assert {issue.code for issue in copied_issues} == {
        IssueCode.MOJIBAKE,
        IssueCode.QUESTION_MARK_RUN,
        IssueCode.REPLACEMENT_CHARACTER,
    }


def test_allowlist_has_a_single_occurrence_quota() -> None:
    """Duplicating an intentional example exceeds its narrow allowance."""

    guidance_rule = next(
        rule
        for rule in DOCUMENTATION_ALLOWLIST
        if rule.code is IssueCode.REPLACEMENT_CHARACTER
    )
    duplicated = guidance_rule.exact_line + "\n" + guidance_rule.exact_line

    issues = scan_text(guidance_rule.path, duplicated)

    assert {issue.code for issue in issues} == {
        IssueCode.MOJIBAKE,
        IssueCode.QUESTION_MARK_RUN,
        IssueCode.REPLACEMENT_CHARACTER,
    }
    assert {issue.line for issue in issues} == {2}


def test_allowlist_cannot_hide_invalid_utf8_or_bom(tmp_path: Path) -> None:
    """Documentation exceptions never bypass decoding and BOM validation."""

    agents_path = PurePosixPath("AGENTS.md")
    (tmp_path / "AGENTS.md").write_bytes(bytes((0xFF,)))
    invalid_issues = scan_file(tmp_path, agents_path)
    bom_issues = scan_text(agents_path, chr(0xFEFF))

    assert tuple(issue.code for issue in invalid_issues) == (IssueCode.INVALID_UTF8,)
    assert IssueCode.UTF8_BOM in {issue.code for issue in bom_issues}


def test_current_agents_document_matches_the_narrow_allowlist() -> None:
    """Intentional repository examples remain exact while other corruption fails."""

    result = scan_paths(_REPOSITORY_ROOT, (PurePosixPath("AGENTS.md"),))

    assert result.files_checked == 1
    assert result.issues == ()


def test_skips_known_binary_file_extensions(tmp_path: Path) -> None:
    """Binary artifacts are excluded instead of being decoded as repository text."""

    binary_path = PurePosixPath("requirements.docx")
    (tmp_path / binary_path.name).write_bytes(bytes((0xFF, 0xFE)))

    result = scan_paths(tmp_path, (binary_path,))

    assert result.files_checked == 0
    assert result.issues == ()


def test_parses_nul_delimited_utf8_paths_without_newline_assumptions() -> None:
    """Git paths with Persian text, spaces, and newlines remain unambiguous."""

    output = "docs/سلام دنیا.md\0docs/line\nbreak.txt\0".encode()

    assert parse_nul_paths(output) == (
        PurePosixPath("docs/سلام دنیا.md"),
        PurePosixPath("docs/line\nbreak.txt"),
    )


def test_rejects_unsafe_git_paths() -> None:
    """Git discovery cannot make the scanner traverse outside the repository."""

    with pytest.raises(TextIntegrityToolError):
        parse_nul_paths(b"../secret.txt\0")


def test_changed_paths_union_staged_unstaged_and_untracked(tmp_path: Path) -> None:
    """Changed discovery unions all three Git states and removes duplicates."""

    commands = (
        ("diff", "--name-only", "--diff-filter=ACMRTUXB", "-z", "--"),
        ("diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB", "-z", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z", "--"),
    )
    outputs = {
        commands[0]: b"docs/unstaged.md\0docs/shared.md\0",
        commands[1]: b"docs/staged.md\0docs/shared.md\0",
        commands[2]: "docs/جدید.md\0".encode(),
    }
    observed_commands: list[tuple[str, ...]] = []

    def runner(repo_root: Path, arguments: tuple[str, ...]) -> bytes:
        assert repo_root == tmp_path
        observed_commands.append(arguments)
        return outputs[arguments]

    paths = collect_changed_paths(tmp_path, runner=runner)

    assert observed_commands == list(commands)
    assert paths == tuple(sorted(set(paths), key=lambda path: path.as_posix()))
    assert set(paths) == {
        PurePosixPath("docs/shared.md"),
        PurePosixPath("docs/staged.md"),
        PurePosixPath("docs/unstaged.md"),
        PurePosixPath("docs/جدید.md"),
    }


def test_all_paths_uses_full_tracked_and_untracked_git_scan(tmp_path: Path) -> None:
    """Full mode has useful semantics on a clean CI checkout."""

    expected_command = (
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
    )

    def runner(repo_root: Path, arguments: tuple[str, ...]) -> bytes:
        assert repo_root == tmp_path
        assert arguments == expected_command
        return b"AGENTS.md\0"

    assert collect_all_paths(tmp_path, runner=runner) == (PurePosixPath("AGENTS.md"),)


def test_persian_json_fixture_round_trips_without_ascii_escaping() -> None:
    """The representative fixture remains byte-exact, readable, and unnormalized."""

    with _FIXTURE_PATH.open(
        "r", encoding="utf-8", errors="strict", newline=""
    ) as fixture:
        raw_text = fixture.read()
    raw_bytes = _FIXTURE_PATH.read_bytes()
    payload = cast("dict[str, str]", json.loads(raw_text))
    expected_message = (
        "سلام، این متن فارسی با نیم\u200cفاصله حفظ می\u200cشود.\n"
        "خط دوم با نشانه\u200cگذاری «درست» و ایموجی 😀✨"
    )

    assert raw_bytes == raw_text.encode("utf-8")
    assert not raw_text.startswith(chr(0xFEFF))
    assert payload == {"message": expected_message}

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    round_tripped = cast("dict[str, str]", json.loads(serialized))

    assert "سلام، این متن فارسی با نیم\u200cفاصله" in serialized
    assert "ایموجی 😀✨" in serialized
    assert "\\u0633" not in serialized
    assert round_tripped == payload
