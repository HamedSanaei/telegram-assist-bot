from __future__ import annotations

from copy import deepcopy

from telegram_assist_bot.shared.observability.redaction import (
    REDACTION_MARKER,
    Redactor,
)

_PRIVATE_SENTINEL = "private" + "-fixture-value"


def test_nested_values_are_redacted_without_mutating_input() -> None:
    original: dict[str, object] = {
        "safe": "متن فارسی پیرامون داده سالم است ✅",
        "nested": {
            "api_key": _PRIVATE_SENTINEL,
            "db_password": _PRIVATE_SENTINEL,
            "X-API-Key": _PRIVATE_SENTINEL,
            "phone_number": "+989120000000",
            "items": [
                {"authorization_header": f"Bearer {_PRIVATE_SENTINEL}"},
                {"post_content": "متن کامل پست نباید لاگ شود"},
            ],
        },
    }
    snapshot = deepcopy(original)

    redacted = Redactor(secret_values=(_PRIVATE_SENTINEL,)).redact(original)

    assert original == snapshot
    assert isinstance(redacted, dict)
    assert redacted["safe"] == "متن فارسی پیرامون داده سالم است ✅"
    nested = redacted["nested"]
    assert isinstance(nested, dict)
    assert nested["api_key"] == REDACTION_MARKER
    assert nested["db_password"] == REDACTION_MARKER
    assert nested["X-API-Key"] == REDACTION_MARKER
    assert nested["phone_number"] == REDACTION_MARKER
    assert nested["items"] == [
        {"authorization_header": REDACTION_MARKER},
        {"post_content": REDACTION_MARKER},
    ]


def test_string_patterns_remove_values_and_preserve_surrounding_persian() -> None:
    user = "synthetic" + "-user"
    credential = "synthetic" + "-credential"
    uri = f"mongodb://{user}:{credential}@database.example.invalid/app"
    value = (
        f"شروع فارسی؛ {uri}؛ Authorization: Bearer {_PRIVATE_SENTINEL}؛ "
        f"token={_PRIVATE_SENTINEL}؛ پایان فارسی ✨"
    )

    redacted = Redactor(secret_values=(_PRIVATE_SENTINEL,)).redact(value)

    assert isinstance(redacted, str)
    assert redacted.startswith("شروع فارسی؛ ")
    assert redacted.endswith("؛ پایان فارسی ✨")
    assert uri not in redacted
    assert user not in redacted
    assert credential not in redacted
    assert _PRIVATE_SENTINEL not in redacted
    assert redacted.count(REDACTION_MARKER) >= 3


def test_quoted_authorization_and_partial_uri_secret_are_fully_redacted() -> None:
    user = "synthetic" + "-user"
    credential = "synthetic" + "-credential"
    uri = f"mongodb://{user}:{credential}@database.example.invalid/app"
    value = f'فارسی Authorization: "Bearer {_PRIVATE_SENTINEL}" سپس {uri} پایان'

    redacted = Redactor(secret_values=(credential,)).redact(value)

    assert isinstance(redacted, str)
    assert redacted.startswith("فارسی Authorization: ")
    assert redacted.endswith(" پایان")
    assert _PRIVATE_SENTINEL not in redacted
    assert user not in redacted
    assert credential not in redacted
    assert uri not in redacted


def test_secret_query_url_is_removed_as_one_value_but_safe_url_is_preserved() -> None:
    unsafe_url = "https://provider.example.invalid/v1?access_token=" + _PRIVATE_SENTINEL
    safe_url = "https://docs.example.invalid/راهنما"

    redacted = Redactor().redact(f"{unsafe_url} سپس {safe_url}")

    assert redacted == f"{REDACTION_MARKER} سپس {safe_url}"


def test_key_query_and_telegram_bot_path_are_known_secret_url_patterns() -> None:
    query_url = "https://provider.example.invalid/v1?key=" + _PRIVATE_SENTINEL
    bot_token = "123456789:" + "synthetic_bot_token_value_12345"
    bot_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    redacted = Redactor().redact(f"{query_url} سپس {bot_url}")

    assert redacted == f"{REDACTION_MARKER} سپس {REDACTION_MARKER}"
    assert _PRIVATE_SENTINEL not in redacted
    assert bot_token not in redacted


def test_exception_messages_use_the_same_redaction_policy() -> None:
    error = RuntimeError(f"خطای موقت token={_PRIVATE_SENTINEL} در مسیر پردازش فارسی")

    redacted = Redactor(secret_values=(_PRIVATE_SENTINEL,)).redact(error)

    assert redacted == {
        "type": "RuntimeError",
        "message": (f"خطای موقت token={REDACTION_MARKER} در مسیر پردازش فارسی"),
    }


def test_cookie_and_session_values_are_removed_from_exception_messages() -> None:
    error = RuntimeError(
        "خطای درخواست Cookie: sessionid=private-cookie-value سپس "
        "session_data=private-session-value پایان"
    )

    redacted = Redactor().redact(error)

    assert isinstance(redacted, dict)
    message = redacted["message"]
    assert isinstance(message, str)
    assert message.startswith("خطای درخواست Cookie: ")
    assert message.endswith(" پایان")
    assert "private-cookie-value" not in message
    assert "private-session-value" not in message
    assert message.count(REDACTION_MARKER) == 2


def test_multi_cookie_and_project_secret_assignments_are_fully_redacted() -> None:
    error = RuntimeError(
        'شروع Cookie: "sessionid=one two" سپس '
        "Cookie: sessionid=one; csrftoken=two پایان؛ "
        "api_hash=private-api-hash phone_number=+989120000000"
    )

    redacted = Redactor().redact(error)

    assert isinstance(redacted, dict)
    message = redacted["message"]
    assert isinstance(message, str)
    assert "one two" not in message
    assert "csrftoken" not in message
    assert "private-api-hash" not in message
    assert "+989120000000" not in message
    assert "شروع" in message
    assert "پایان" in message
    assert message.count(REDACTION_MARKER) == 4


def test_recursive_and_deep_structures_are_bounded_deterministically() -> None:
    recursive: list[object] = []
    recursive.append(recursive)
    deep = {"first": {"second": "visible only beyond the configured depth"}}

    redactor = Redactor(max_depth=2)

    assert redactor.redact(recursive) == [REDACTION_MARKER]
    assert redactor.redact(deep) == {"first": {"second": REDACTION_MARKER}}


def test_redactor_repr_and_unknown_objects_do_not_expose_values() -> None:
    class PrivateObject:
        def __repr__(self) -> str:
            return _PRIVATE_SENTINEL

        def __str__(self) -> str:
            return _PRIVATE_SENTINEL

    redactor = Redactor(secret_values=(_PRIVATE_SENTINEL,))

    assert _PRIVATE_SENTINEL not in repr(redactor)
    assert redactor.redact(PrivateObject()) == "<PrivateObject>"
