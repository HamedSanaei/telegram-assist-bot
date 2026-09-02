"""Behavior tests for locked transactional operator configuration changes."""

from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.bootstrap.cli import main
from telegram_assist_bot.bootstrap.instance_config import (
    InstanceConfigurationError,
    render_instance_configuration,
)
from telegram_assist_bot.bootstrap.operator_config import (
    ConfigMutationConflictError,
    ConfigTransactionError,
    add_administrators,
    add_destination,
    add_sources,
    configure_source,
    mutate_configuration_transaction,
    remove_administrator,
    remove_destination,
    remove_source,
    set_administrator_active,
    set_administrator_destinations,
    set_approval_chat_id,
    set_destination_active,
    set_logging_level,
    set_media_cleanup_interval,
    set_media_preview_enabled,
    set_media_retention,
    set_source_active,
    set_timezone,
)
from telegram_assist_bot.shared.config import ApplicationConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

ROOT = Path(__file__).parents[3]
ENVIRONMENT = {
    "TAB_MONGODB_URI": "mongodb://localhost:27017",
    "TAB_TELEGRAM_API_ID": "12345",
    "TAB_TELEGRAM_API_HASH": "0" * 32,
    "TAB_TELEGRAM_PHONE_NUMBER": "+10000000000",
    "TAB_TELEGRAM_BOT_TOKEN": "123456:" + ("A" * 35),
}


class _Controller:
    def __init__(self, *, healthy: bool = True, fail_restart: bool = False) -> None:
        self.is_healthy = healthy
        self.fail_restart = fail_restart
        self.restarts: list[tuple[str, ...]] = []

    def restart(self, services: Sequence[str]) -> None:
        self.restarts.append(tuple(services))
        if self.fail_restart:
            self.fail_restart = False
            raise RuntimeError("synthetic restart failure")

    def healthy(self, services: Sequence[str]) -> bool:
        return self.is_healthy


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config" / "configuration.json"
    render_instance_configuration(
        template_path=ROOT / "config" / "configuration.example.json",
        output_path=path,
        instance="operator",
        retention_days=2,
        approval_chat_id=-1009001,
        admin_user_ids=(7001,),
        source_usernames=("sourceone",),
        destination_name="مقصد اصلی",
        destination_id=-1009002,
        destination_username=None,
        timezone="Asia/Tehran",
    )
    return path


def _run(
    path: Path,
    mutator: Callable[[dict[str, Any]], None],
    *,
    controller: _Controller | None = None,
) -> Path:
    return mutate_configuration_transaction(
        config_path=path,
        backup_directory=path.parent.parent / "backups" / "config",
        environ=ENVIRONMENT,
        mutator=mutator,
        affected_services=("runtime",),
        controller=controller,
    )


def _load(path: Path) -> ApplicationConfig:
    return ApplicationConfig.model_validate_json(path.read_text(encoding="utf-8"))


def test_transaction_creates_backup_preserves_mode_and_utf8(
    config_path: Path,
) -> None:
    original = config_path.read_bytes()
    original_mode = stat.S_IMODE(config_path.stat().st_mode)
    backup = _run(config_path, set_media_retention(7))

    assert backup.read_bytes() == original
    assert _load(config_path).media.retention_days == 7
    assert "مقصد اصلی" in config_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(config_path.stat().st_mode) == original_mode


def test_restart_failure_rolls_back_original_bytes(config_path: Path) -> None:
    original = config_path.read_bytes()
    controller = _Controller(fail_restart=True)

    with pytest.raises(ConfigTransactionError, match="rolled back"):
        _run(config_path, set_media_retention(8), controller=controller)

    assert config_path.read_bytes() == original
    assert len(controller.restarts) == 2


def test_unhealthy_services_roll_back_original_bytes(config_path: Path) -> None:
    original = config_path.read_bytes()
    with pytest.raises(ConfigTransactionError, match="healthy"):
        _run(
            config_path,
            set_media_retention(8),
            controller=_Controller(healthy=False),
        )
    assert config_path.read_bytes() == original


def test_invalid_candidate_never_replaces_current_config(config_path: Path) -> None:
    original = config_path.read_bytes()

    def invalid(document: dict[str, Any]) -> None:
        document["media"]["retention_days"] = 0

    with pytest.raises(ConfigTransactionError, match="invalid"):
        _run(config_path, invalid)
    assert config_path.read_bytes() == original


def test_duplicate_admin_and_last_active_admin_are_rejected(config_path: Path) -> None:
    with pytest.raises(ConfigMutationConflictError, match="already exists"):
        _run(
            config_path,
            add_administrators("7001", allowed_destinations=("مقصد اصلی",)),
        )
    with pytest.raises(ConfigMutationConflictError, match="last active"):
        _run(config_path, remove_administrator(7001))
    with pytest.raises(ConfigMutationConflictError, match="last active"):
        _run(config_path, set_administrator_active(7001, active=False))


def test_admin_and_source_additions_are_typed(config_path: Path) -> None:
    _run(
        config_path,
        add_administrators("7002,7003", allowed_destinations=("مقصد اصلی",)),
    )
    _run(
        config_path,
        add_sources(
            "@SourceTwo,https://t.me/SourceThree",
            allowed_destinations=("مقصد اصلی",),
        ),
    )
    loaded = _load(config_path)
    assert [item.telegram_user_id for item in loaded.admins] == [7001, 7002, 7003]
    assert [item.username for item in loaded.source_channels] == [
        "sourceone",
        "sourcetwo",
        "sourcethree",
    ]


def test_repeated_source_state_is_idempotent(config_path: Path) -> None:
    first = _run(config_path, set_source_active("sourceone", active=False))
    first_result = config_path.read_bytes()
    second = _run(config_path, set_source_active("sourceone", active=False))
    assert config_path.read_bytes() == first_result
    assert first.exists()
    assert second.exists()


def test_referenced_destination_removal_is_rejected(config_path: Path) -> None:
    with pytest.raises(ConfigMutationConflictError, match="referenced"):
        _run(config_path, remove_destination("مقصد اصلی"))


def test_destination_identity_conflicts_are_rejected(config_path: Path) -> None:
    _run(
        config_path,
        add_destination(name="secondary", telegram_channel_id=-1009003),
    )
    with pytest.raises(ConfigMutationConflictError, match="already exists"):
        _run(
            config_path,
            add_destination(name="third", telegram_channel_id=-1009003),
        )


def test_concurrent_mutations_cannot_corrupt_json(config_path: Path) -> None:
    def change(days: int) -> None:
        _run(config_path, set_media_retention(days))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(change, day) for day in (5, 6)]
        for future in futures:
            future.result()

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    loaded = ApplicationConfig.model_validate(raw)
    assert loaded.media.retention_days in {5, 6}
    assert len(list((config_path.parent.parent / "backups" / "config").iterdir())) == 2


def test_administrator_destination_and_state_mutations(config_path: Path) -> None:
    _run(
        config_path,
        add_destination(name="secondary", telegram_channel_id=-1009003),
    )
    _run(
        config_path,
        set_administrator_destinations(7001, ("secondary",)),
    )
    loaded = _load(config_path)
    assert loaded.admins[0].allowed_destination_names == ("secondary",)
    assert loaded.admins[0].allowed_destination_ids == (-1009003,)

    with pytest.raises(ConfigMutationConflictError, match="invalid"):
        _run(
            config_path,
            set_administrator_destinations(7001, ("missing",)),
        )
    with pytest.raises(ConfigMutationConflictError, match="does not exist"):
        _run(config_path, set_administrator_active(9999, active=True))

    before = config_path.read_bytes()
    _run(config_path, set_administrator_active(7001, active=True))
    assert config_path.read_bytes() == before


def test_source_configuration_conflicts_and_removal(config_path: Path) -> None:
    _run(
        config_path,
        add_sources("SourceTwo", allowed_destinations=("مقصد اصلی",)),
    )
    _run(
        config_path,
        configure_source(
            "SourceTwo",
            telegram_channel_id=-1008001,
            allowed_destinations=("مقصد اصلی",),
        ),
    )
    loaded = _load(config_path)
    assert loaded.source_channels[1].telegram_channel_id == -1008001

    _run(
        config_path,
        configure_source("sourceone", telegram_channel_id=-1008002),
    )
    with pytest.raises(ConfigMutationConflictError, match="already exists"):
        _run(
            config_path,
            configure_source("SourceTwo", telegram_channel_id=-1008002),
        )
    with pytest.raises(ConfigMutationConflictError, match="invalid"):
        _run(
            config_path,
            configure_source("SourceTwo", telegram_channel_id=0),
        )
    with pytest.raises(ConfigMutationConflictError, match="does not exist"):
        _run(config_path, configure_source("MissingSource"))
    with pytest.raises(ConfigMutationConflictError, match="destinations"):
        _run(
            config_path,
            configure_source("SourceTwo", allowed_destinations=("missing",)),
        )

    _run(config_path, remove_source("SourceTwo"))
    with pytest.raises(ConfigMutationConflictError, match="At least one source"):
        _run(config_path, remove_source("sourceone"))
    with pytest.raises(ConfigMutationConflictError, match="does not exist"):
        _run(config_path, set_source_active("MissingSource", active=True))


def test_destination_state_removal_and_validation(config_path: Path) -> None:
    with pytest.raises(ConfigMutationConflictError, match="invalid"):
        _run(config_path, add_destination(name="", telegram_channel_id=0))
    _run(
        config_path,
        add_destination(
            name="secondary",
            telegram_channel_id=-1009003,
            username="@SecondaryChannel",
            enabled=False,
        ),
    )
    _run(config_path, set_destination_active("secondary", active=True))
    assert _load(config_path).destination_channels[1].enabled is True
    with pytest.raises(ConfigMutationConflictError, match="does not exist"):
        _run(config_path, set_destination_active("missing", active=True))

    _run(config_path, remove_destination("secondary"))
    candidate = json.loads(config_path.read_text(encoding="utf-8"))
    candidate["admins"][0]["allowed_destination_names"] = []
    candidate["source_channels"][0]["allowed_destination_names"] = []
    with pytest.raises(ConfigMutationConflictError, match="At least one destination"):
        remove_destination("مقصد اصلی")(candidate)


def test_logging_validation_and_invalid_current_file(config_path: Path) -> None:
    _run(config_path, set_logging_level("DEBUG"))
    assert _load(config_path).logging.level == "DEBUG"
    with pytest.raises(ConfigTransactionError, match="invalid"):
        _run(config_path, set_logging_level("VERBOSE"))

    config_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigTransactionError, match="cannot be read"):
        _run(config_path, set_media_retention(3))


@pytest.mark.parametrize(
    ("operation", "value", "destinations", "expected"),
    [
        ("admin-add", "7002", "مقصد اصلی", 0),
        ("admin-remove", "7001", "", 2),
        ("admin-enable", "7001", "", 0),
        ("admin-disable", "7001", "", 2),
        ("source-add", "SourceTwo", "مقصد اصلی", 0),
        ("source-remove", "sourceone", "", 2),
        ("source-enable", "sourceone", "", 0),
        ("source-disable", "sourceone", "", 0),
        (
            "destination-add",
            '{"name":"secondary","telegram_channel_id":-1009003}',
            "",
            0,
        ),
        ("destination-remove", "مقصد اصلی", "", 2),
        ("destination-enable", "مقصد اصلی", "", 0),
        ("destination-disable", "مقصد اصلی", "", 0),
        ("retention-set", "3", "", 0),
        ("logging-set", "INFO", "", 0),
        ("timezone-set", "UTC", "", 0),
        ("preview-set", "true", "", 0),
        ("cleanup-interval-set", "1800", "", 0),
        ("approval-chat-set", "-1009999", "", 0),
        ("timezone-set", "Not/AZone", "", 2),
        ("preview-set", "maybe", "", 2),
        ("cleanup-interval-set", "5", "", 2),
        ("approval-chat-set", "123", "", 2),
    ],
)
def test_operator_cli_dispatches_each_typed_operation(
    tmp_path: Path,
    operation: str,
    value: str,
    destinations: str,
    expected: int,
) -> None:
    path = tmp_path / operation / "config" / "configuration.json"
    render_instance_configuration(
        template_path=ROOT / "config" / "configuration.example.json",
        output_path=path,
        instance="cliops",
        retention_days=2,
        approval_chat_id=-1009001,
        admin_user_ids=(7001,),
        source_usernames=("sourceone",),
        destination_name="مقصد اصلی",
        destination_id=-1009002,
        destination_username=None,
        timezone="Asia/Tehran",
    )
    arguments = [
        "operator-config",
        "--config",
        str(path),
        "--operation",
        operation,
        "--value",
        value,
    ]
    if destinations:
        arguments.extend(["--destinations", destinations])
    assert main(arguments, environ=ENVIRONMENT) == expected


def test_typed_settings_mutators_validate_and_apply(config_path: Path) -> None:
    _run(config_path, set_timezone("Asia/Tehran"))
    _run(config_path, set_media_preview_enabled(True))
    _run(config_path, set_media_cleanup_interval(1800))
    _run(config_path, set_approval_chat_id(-1005001))
    config = _load(config_path)
    assert str(config.timezone) == "Asia/Tehran"
    assert config.media.preview_enabled is True
    assert config.media.cleanup_interval_seconds == 1800
    assert config.telegram.bot.approval_chat_id == -1005001

    with pytest.raises(InstanceConfigurationError):
        set_timezone("Not/AZone")
    with pytest.raises(InstanceConfigurationError):
        set_media_cleanup_interval(5)
    with pytest.raises(InstanceConfigurationError):
        set_approval_chat_id(0)
    with pytest.raises(InstanceConfigurationError):
        set_media_preview_enabled("yes")  # type: ignore[arg-type]


def test_operator_cli_rejects_missing_and_nonnumeric_values(
    config_path: Path,
) -> None:
    assert main(["operator-config"], environ=ENVIRONMENT) == 2
    assert (
        main(
            [
                "operator-config",
                "--config",
                str(config_path),
                "--operation",
                "retention-set",
                "--value",
                "not-a-number",
            ],
            environ=ENVIRONMENT,
        )
        == 2
    )
