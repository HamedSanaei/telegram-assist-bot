"""Deployment compatibility policy for Linux kernels and MongoDB images."""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERSION_PREFIX = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?")
_IMAGE_TAG = re.compile(
    r"^(?P<repository>[A-Za-z0-9._:/-]+):"
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$",
    re.ASCII,
)

DEFAULT_MONGODB_IMAGE = "mongo:7.0.32"
"""Production-tested MongoDB image used until a fixed 8.x is verified."""


class DeploymentCompatibilityError(ValueError):
    """Report a safe, actionable deployment compatibility failure."""


@dataclass(frozen=True, order=True, slots=True)
class NumericVersion:
    """Comparable numeric version prefix."""

    major: int
    minor: int
    patch: int = 0


@dataclass(frozen=True, slots=True)
class MongoCompatibilityDecision:
    """Result of evaluating one selected MongoDB image on one Linux kernel."""

    kernel: NumericVersion
    mongodb: NumericVersion
    image: str
    compatible: bool
    reason: str


def parse_kernel_version(value: str) -> NumericVersion:
    """Parse the numeric prefix of one Linux kernel release."""
    match = _VERSION_PREFIX.match(value.strip())
    if match is None:
        raise DeploymentCompatibilityError(
            "Linux kernel version must begin with MAJOR.MINOR."
        )
    patch = match.group("patch")
    return NumericVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=0 if patch is None else int(patch),
    )


def parse_mongodb_image_version(image: str) -> NumericVersion:
    """Require an immutable-looking MongoDB image tag with a patch version."""
    match = _IMAGE_TAG.fullmatch(image.strip())
    if match is None:
        raise DeploymentCompatibilityError(
            "MongoDB image must use an explicit MAJOR.MINOR.PATCH tag."
        )
    return NumericVersion(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
    )


def evaluate_mongodb_compatibility(
    *,
    kernel_release: str,
    mongodb_image: str,
) -> MongoCompatibilityDecision:
    """Evaluate the explicit MongoDB image against the supported kernel policy."""
    kernel = parse_kernel_version(kernel_release)
    mongodb = parse_mongodb_image_version(mongodb_image)
    incompatible_boundary = NumericVersion(6, 19)
    if kernel >= incompatible_boundary and mongodb.major == 8:
        return MongoCompatibilityDecision(
            kernel=kernel,
            mongodb=mongodb,
            image=mongodb_image,
            compatible=False,
            reason=(
                "MongoDB 8.x is blocked on Linux kernel 6.19+ until an "
                "upstream-fixed release is explicitly verified."
            ),
        )
    return MongoCompatibilityDecision(
        kernel=kernel,
        mongodb=mongodb,
        image=mongodb_image,
        compatible=True,
        reason="Selected MongoDB image is allowed by the production kernel policy.",
    )


def format_compatibility_report(decision: MongoCompatibilityDecision) -> str:
    """Render a stable non-secret operator report."""
    kernel = f"{decision.kernel.major}.{decision.kernel.minor}.{decision.kernel.patch}"
    status = "compatible" if decision.compatible else "blocked"
    return "\n".join(
        (
            f"detected_kernel={kernel}",
            f"selected_mongodb_image={decision.image}",
            f"compatibility_decision={status}: {decision.reason}",
        )
    )


def require_mongodb_compatibility(
    *,
    kernel_release: str,
    mongodb_image: str,
) -> str:
    """Return the report or raise when the selected pair is unsafe."""
    decision = evaluate_mongodb_compatibility(
        kernel_release=kernel_release,
        mongodb_image=mongodb_image,
    )
    report = format_compatibility_report(decision)
    if not decision.compatible:
        raise DeploymentCompatibilityError(report)
    return report


__all__ = (
    "DEFAULT_MONGODB_IMAGE",
    "DeploymentCompatibilityError",
    "MongoCompatibilityDecision",
    "NumericVersion",
    "evaluate_mongodb_compatibility",
    "format_compatibility_report",
    "parse_kernel_version",
    "parse_mongodb_image_version",
    "require_mongodb_compatibility",
)
