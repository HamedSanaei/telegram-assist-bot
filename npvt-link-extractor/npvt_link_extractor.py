#!/usr/bin/env python3

"""NPVT Link Extractor

- Reads decrypted Pantegnos .txt output directly.
- Can also accept an .npvt file when pantegnos-win.exe is placed next to this script.
- Extracts NapsternetV custom JSON profiles.
- Converts supported profiles to trojan://, vless://, vmess:// and ss:// links.
- Writes:
    links.txt
    subscription.txt
    normalized.json
    report.txt
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def find_pantegnos(script_dir: Path) -> Path | None:
    candidates = [
        script_dir / "pantegnos-win.exe",
        script_dir / "pantegnos.exe",
        script_dir / "pantegnos-win-amd64.exe",
        script_dir / "pantegnos-windows.exe",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    for name in (
        "pantegnos-win.exe",
        "pantegnos.exe",
        "pantegnos-win-amd64.exe",
        "pantegnos-windows.exe",
    ):
        found = shutil.which(name)
        if found:
            return Path(found)

    return None


def decrypt_npvt(npvt_path: Path, pantegnos_path: Path) -> Path:
    """Pantegnos commonly expects input/output directories rather than one file.
    Copy the requested file into a temporary input folder and locate the
    generated text file afterward.
    """
    temp_root = Path(tempfile.mkdtemp(prefix="npvt-extractor-"))
    input_dir = temp_root / "input"
    output_dir = temp_root / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    copied = input_dir / npvt_path.name
    shutil.copy2(npvt_path, copied)

    commands = [
        [
            str(pantegnos_path),
            "-input",
            str(input_dir),
            "-output",
            str(output_dir),
        ],
        [
            str(pantegnos_path),
            "-i",
            str(input_dir),
            "-o",
            str(output_dir),
        ],
    ]

    errors: list[str] = []

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except Exception as exc:
            errors.append(f"{' '.join(command)} -> {exc}")
            continue

        txt_files = sorted(output_dir.rglob("*.txt"))
        if txt_files:
            return txt_files[0]

        # Some releases may write beside the source or use another extension.
        candidate_files = [
            path
            for path in output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() not in {".npvt", ".exe"}
        ]
        if candidate_files:
            return candidate_files[0]

        errors.append(
            f"Command returned {result.returncode}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    raise RuntimeError(
        "Pantegnos could not decrypt the NPVT file.\n\n" + "\n\n".join(errors)
    )


def iter_json_values(text: str):
    """Pantegnos output may contain multiple JSON values concatenated together,
    repeated arrays, leading counters such as '1', and non-JSON console text.
    Scan the complete text and yield every valid JSON value.
    """
    decoder = json.JSONDecoder()
    index = 0
    length = len(text)

    while index < length:
        if text[index] not in "[{":
            index += 1
            continue

        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue

        yield value
        index += consumed


def collect_profiles(value: Any) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []

    if isinstance(value, list):
        for item in value:
            profiles.extend(collect_profiles(item))
        return profiles

    if not isinstance(value, dict):
        return profiles

    if isinstance(value.get("v2rayProfile"), dict):
        profiles.append(value)
        return profiles

    # Also support normalized/simpler profile objects, but do not mistake
    # lockConfig objects for proxy profiles merely because they contain a
    # "password" field.
    has_server = any(
        str(value.get(key, "")).strip() for key in ("server", "address", "hostName")
    )
    has_port = any(str(value.get(key, "")).strip() for key in ("serverPort", "port"))
    has_credential = any(
        str(value.get(key, "")).strip()
        for key in ("uuid", "id", "userId", "password", "passwd")
    )
    has_protocol_hint = any(
        str(value.get(key, "")).strip()
        for key in ("protocol", "type", "configProtocol", "method", "cipher")
    )

    if has_server and has_port and (has_credential or has_protocol_hint):
        profiles.append({"name": value.get("remarks", ""), "v2rayProfile": value})
        return profiles

    for nested in value.values():
        if isinstance(nested, (dict, list)):
            profiles.extend(collect_profiles(nested))

    return profiles


def first_nonempty(mapping: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return default


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("v2rayProfile")
    if not isinstance(profile, dict):
        profile = item

    name = first_nonempty(
        profile,
        "remarks",
        "name",
        default=first_nonempty(item, "name", "remarks", default="Proxy"),
    )

    normalized = {
        "name": name,
        "protocol": first_nonempty(profile, "protocol", "type", "configProtocol"),
        "server": first_nonempty(profile, "server", "address", "hostName"),
        "port": first_nonempty(profile, "serverPort", "port"),
        "uuid": first_nonempty(profile, "uuid", "id", "userId"),
        "password": first_nonempty(profile, "password", "passwd"),
        "encryption": first_nonempty(profile, "encryption", default="none"),
        "method": first_nonempty(profile, "method", "cipher"),
        "network": first_nonempty(profile, "network", "transport", default="tcp"),
        "host": first_nonempty(profile, "host", "wsHost"),
        "path": first_nonempty(profile, "path", "wsPath"),
        "security": first_nonempty(profile, "security", default="none"),
        "sni": first_nonempty(profile, "sni", "serverName"),
        "alpn": first_nonempty(profile, "alpn"),
        "fingerprint": first_nonempty(profile, "fingerPrint", "fingerprint", "fp"),
        "flow": first_nonempty(profile, "flow"),
        "service_name": first_nonempty(profile, "serviceName", "service_name"),
        "authority": first_nonempty(profile, "authority"),
        "insecure": bool(profile.get("insecure", False)),
        "config_type": profile.get("configType"),
        "raw": profile,
    }

    normalized["protocol"] = detect_protocol(normalized)
    return normalized


def detect_protocol(profile: dict[str, Any]) -> str:
    explicit = str(profile.get("protocol") or "").strip().lower()
    aliases = {
        "shadowsocks": "ss",
        "shadow socks": "ss",
        "trojan-go": "trojan",
    }

    if explicit in aliases:
        return aliases[explicit]
    if explicit in {"vless", "vmess", "trojan", "ss"}:
        return explicit

    raw = profile.get("raw") or {}
    config_type = raw.get("configType")

    # NapsternetV exports often omit the protocol name.
    # Strong field-based detection is safer than relying on configType alone.
    if profile.get("method") and profile.get("password"):
        return "ss"

    if profile.get("uuid"):
        if str(raw.get("alterId", "")).strip() or "alterId" in raw or "aid" in raw:
            return "vmess"
        return "vless"

    if profile.get("password"):
        return "trojan"

    # Known fallback from some NapsternetV profile variants.
    if config_type in {1, "1"}:
        return "vmess"
    if config_type in {2, "2"}:
        return "vless"
    if config_type in {6, "6"}:
        return "trojan"

    return "unknown"


def common_transport_params(profile: dict[str, Any]) -> dict[str, str]:
    params: dict[str, str] = {}

    security = str(profile.get("security") or "none")
    network = str(profile.get("network") or "tcp")

    params["security"] = security
    params["type"] = network

    if profile.get("host"):
        params["host"] = str(profile["host"])
    if profile.get("path"):
        params["path"] = str(profile["path"])
    if profile.get("sni"):
        params["sni"] = str(profile["sni"])
    if profile.get("alpn"):
        params["alpn"] = str(profile["alpn"])
    if profile.get("fingerprint"):
        params["fp"] = str(profile["fingerprint"])
    if profile.get("flow"):
        params["flow"] = str(profile["flow"])
    if profile.get("service_name"):
        params["serviceName"] = str(profile["service_name"])
    if profile.get("authority"):
        params["authority"] = str(profile["authority"])
    if profile.get("insecure"):
        params["allowInsecure"] = "1"

    return params


def build_trojan(profile: dict[str, Any]) -> str:
    password = str(profile["password"])
    server = str(profile["server"])
    port = str(profile["port"])
    name = str(profile["name"])

    params = common_transport_params(profile)
    query = urlencode(params, quote_via=quote, safe="")

    return (
        f"trojan://{quote(password, safe='')}@{server}:{port}"
        f"?{query}#{quote(name, safe='')}"
    )


def build_vless(profile: dict[str, Any]) -> str:
    uuid = str(profile["uuid"])
    server = str(profile["server"])
    port = str(profile["port"])
    name = str(profile["name"])

    params = common_transport_params(profile)
    params["encryption"] = str(profile.get("encryption") or "none")

    query = urlencode(params, quote_via=quote, safe="")
    return (
        f"vless://{quote(uuid, safe='')}@{server}:{port}?{query}#{quote(name, safe='')}"
    )


def build_vmess(profile: dict[str, Any]) -> str:
    raw = profile.get("raw") or {}

    payload = {
        "v": "2",
        "ps": str(profile["name"]),
        "add": str(profile["server"]),
        "port": str(profile["port"]),
        "id": str(profile["uuid"]),
        "aid": str(raw.get("alterId", raw.get("aid", 0))),
        "scy": str(raw.get("security", raw.get("cipher", "auto"))),
        "net": str(profile.get("network") or "tcp"),
        "type": str(raw.get("headerType", "none")),
        "host": str(profile.get("host") or ""),
        "path": str(profile.get("path") or ""),
        "tls": "tls"
        if str(profile.get("security") or "").lower() in {"tls", "reality"}
        else "",
        "sni": str(profile.get("sni") or ""),
        "alpn": str(profile.get("alpn") or ""),
        "fp": str(profile.get("fingerprint") or ""),
    }

    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")

    return f"vmess://{encoded}"


def build_ss(profile: dict[str, Any]) -> str:
    method = str(profile["method"])
    password = str(profile["password"])
    server = str(profile["server"])
    port = str(profile["port"])
    name = str(profile["name"])

    userinfo = (
        base64.urlsafe_b64encode(f"{method}:{password}".encode())
        .decode("ascii")
        .rstrip("=")
    )

    return f"ss://{userinfo}@{server}:{port}#{quote(name, safe='')}"


def profile_key(profile: dict[str, Any]) -> tuple[Any, ...]:
    return (
        profile.get("protocol"),
        profile.get("server"),
        profile.get("port"),
        profile.get("uuid"),
        profile.get("password"),
        profile.get("method"),
        profile.get("network"),
        profile.get("host"),
        profile.get("path"),
        profile.get("security"),
        profile.get("sni"),
    )


def convert_profile(profile: dict[str, Any]) -> str:
    required_base = ("server", "port")
    for field in required_base:
        if not profile.get(field):
            raise ValueError(f"Missing required field: {field}")

    protocol = profile["protocol"]

    if protocol == "trojan":
        if not profile.get("password"):
            raise ValueError("Trojan profile has no password")
        return build_trojan(profile)

    if protocol == "vless":
        if not profile.get("uuid"):
            raise ValueError("VLESS profile has no UUID")
        return build_vless(profile)

    if protocol == "vmess":
        if not profile.get("uuid"):
            raise ValueError("VMess profile has no UUID")
        return build_vmess(profile)

    if protocol == "ss":
        if not profile.get("method") or not profile.get("password"):
            raise ValueError("Shadowsocks profile requires method and password")
        return build_ss(profile)

    raise ValueError("Unknown or unsupported protocol")


def process_text(text: str) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    raw_items: list[dict[str, Any]] = []

    for value in iter_json_values(text):
        raw_items.extend(collect_profiles(value))

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for item in raw_items:
        profile = normalize_item(item)
        key = profile_key(profile)

        if key in seen:
            continue

        seen.add(key)
        normalized.append(profile)

    links: list[str] = []
    errors: list[str] = []

    for index, profile in enumerate(normalized, start=1):
        try:
            links.append(convert_profile(profile))
        except Exception as exc:
            errors.append(f"{index}. {profile.get('name', 'Unknown')}: {exc}")

    return normalized, links, errors


def write_outputs(
    source_path: Path,
    output_dir: Path,
    profiles: list[dict[str, Any]],
    links: list[str],
    errors: list[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    links_text = "\n".join(links)
    if links_text:
        links_text += "\n"

    (output_dir / "links.txt").write_text(
        links_text,
        encoding="utf-8",
    )

    subscription = base64.b64encode(links_text.encode("utf-8")).decode("ascii")
    (output_dir / "subscription.txt").write_text(
        subscription + ("\n" if subscription else ""),
        encoding="utf-8",
    )

    serializable_profiles = []
    for profile in profiles:
        item = {key: value for key, value in profile.items() if key != "raw"}
        serializable_profiles.append(item)

    (output_dir / "normalized.json").write_text(
        json.dumps(serializable_profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    protocol_counts: dict[str, int] = {}
    for profile in profiles:
        protocol = str(profile.get("protocol") or "unknown")
        protocol_counts[protocol] = protocol_counts.get(protocol, 0) + 1

    report_lines = [
        "NPVT Link Extractor Report",
        "=" * 30,
        f"Source: {source_path}",
        f"Unique profiles found: {len(profiles)}",
        f"Links generated: {len(links)}",
        f"Conversion errors: {len(errors)}",
        "",
        "Protocol counts:",
    ]

    if protocol_counts:
        for protocol, count in sorted(protocol_counts.items()):
            report_lines.append(f"- {protocol}: {count}")
    else:
        report_lines.append("- none")

    if errors:
        report_lines.extend(["", "Errors:", *errors])

    (output_dir / "report.txt").write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract proxy links from NapsternetV/Pantegnos JSON output "
            "or decrypt an NPVT file through a local Pantegnos executable."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to a .txt Pantegnos output or an .npvt file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output directory. Defaults to <input-name>-extracted",
    )
    parser.add_argument(
        "--pantegnos",
        type=Path,
        help="Optional explicit path to pantegnos-win.exe",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    input_path = args.input.expanduser().resolve()
    if not input_path.exists():
        print(f"Input file does not exist: {input_path}", file=sys.stderr)
        return 2

    output_dir = (
        args.output.expanduser().resolve()
        if args.output
        else input_path.parent / f"{input_path.stem}-extracted"
    )

    actual_text_path = input_path

    try:
        if input_path.suffix.lower() == ".npvt":
            pantegnos = (
                args.pantegnos.expanduser().resolve()
                if args.pantegnos
                else find_pantegnos(Path(__file__).resolve().parent)
            )

            if pantegnos is None or not pantegnos.exists():
                print(
                    "This NPVT file must first be decrypted.\n"
                    "Place pantegnos-win.exe next to this script, or pass:\n"
                    '  --pantegnos "C:\\path\\to\\pantegnos-win.exe"',
                    file=sys.stderr,
                )
                return 3

            print(f"Decrypting with: {pantegnos}")
            actual_text_path = decrypt_npvt(input_path, pantegnos)

        text = read_text(actual_text_path)
        profiles, links, errors = process_text(text)
        write_outputs(input_path, output_dir, profiles, links, errors)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"Unique profiles: {len(profiles)}")
    print(f"Links generated: {len(links)}")
    print(f"Errors: {len(errors)}")
    print(f"Output folder: {output_dir}")
    print()
    print("Created files:")
    print("  links.txt")
    print("  subscription.txt")
    print("  normalized.json")
    print("  report.txt")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
