"""Registry for loading and validating versioned prompts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.schemas import get_expected_schema_version


class PromptMetadata(NamedTuple):
    """Immutable representation of a versioned prompt template."""

    task_type: AITaskType
    prompt_version: str
    schema_version: str
    prompt_hash: str
    body: str


def parse_prompt_file(content: str) -> tuple[dict[str, str], str]:
    """Parse a simple YAML-like frontmatter metadata from prompt content."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            metadata_str = parts[1]
            body = parts[2].strip()
            metadata = {}
            for line in metadata_str.strip().splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    metadata[key.strip()] = val.strip()
            return metadata, body
    return {}, content.strip()


def calculate_prompt_hash(body: str) -> str:
    """Calculate a deterministic SHA-256 hash of the prompt body.

    Normalizes line endings to LF before hashing to ensure consistency across
    different platforms (Windows vs. Linux).
    """
    normalized_body = body.replace("\r\n", "\n")
    return hashlib.sha256(normalized_body.encode("utf-8")).hexdigest()


class PromptRegistry:
    """Immutable registry of validated prompt templates loaded from files."""

    def __init__(self, prompts_dir: Path | str | None = None) -> None:
        """Initialize the registry by loading prompts from the specified directory.

        If prompts_dir is None, loads from the default package prompts folder.
        """
        if prompts_dir is None:
            prompts_dir = Path(__file__).parent / "prompts"

        self._prompts: dict[tuple[AITaskType, str], PromptMetadata] = {}
        self._load_and_validate(Path(prompts_dir))

    def _load_and_validate(self, prompts_dir: Path) -> None:
        if not prompts_dir.exists() or not prompts_dir.is_dir():
            raise FileNotFoundError(f"Prompts directory not found at: {prompts_dir}")

        for file_path in prompts_dir.glob("*.txt"):
            content = file_path.read_text(encoding="utf-8")
            metadata, body = parse_prompt_file(content)

            # Metadata validation
            if "task_type" not in metadata:
                raise ValueError(f"Missing 'task_type' in prompt file {file_path.name}")
            if "prompt_version" not in metadata:
                raise ValueError(
                    f"Missing 'prompt_version' in prompt file {file_path.name}"
                )
            if "schema_version" not in metadata:
                raise ValueError(
                    f"Missing 'schema_version' in prompt file {file_path.name}"
                )

            # Validate task type
            try:
                task_type = AITaskType(metadata["task_type"])
            except ValueError as e:
                raise ValueError(
                    f"Invalid task_type '{metadata['task_type']}' "
                    f"in prompt file {file_path.name}"
                ) from e

            prompt_version = metadata["prompt_version"]
            schema_version = metadata["schema_version"]

            # Validate duplicate prompt versions
            key = (task_type, prompt_version)
            if key in self._prompts:
                raise ValueError(
                    f"Duplicate prompt version '{prompt_version}' "
                    f"for task '{task_type}' in {file_path.name}"
                )

            # Validate schema version compatibility
            expected_schema_version = get_expected_schema_version(task_type)
            if schema_version != expected_schema_version:
                raise ValueError(
                    f"Schema version mismatch for task '{task_type}' "
                    f"in {file_path.name}: got '{schema_version}', "
                    f"expected '{expected_schema_version}'"
                )

            # Calculate deterministic hash
            prompt_hash = calculate_prompt_hash(body)

            self._prompts[key] = PromptMetadata(
                task_type=task_type,
                prompt_version=prompt_version,
                schema_version=schema_version,
                prompt_hash=prompt_hash,
                body=body,
            )

    def get_prompt(self, task_type: AITaskType, prompt_version: str) -> PromptMetadata:
        """Get the validated prompt metadata for the given task and version."""
        key = (task_type, prompt_version)
        if key not in self._prompts:
            raise KeyError(
                f"Prompt for task '{task_type}' and version "
                f"'{prompt_version}' not registered"
            )
        return self._prompts[key]

    def list_prompts(self) -> list[PromptMetadata]:
        """List all loaded and validated prompts."""
        return list(self._prompts.values())
