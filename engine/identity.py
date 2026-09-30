"""Transport identity keys; game progress still uses Character.uid internally."""

PLATFORMS = frozenset({"telegram", "max"})


def identity_key(platform: str, external_user_id) -> tuple[str, str]:
    """Validate an opaque messenger user ID without conflating namespaces."""
    if platform not in PLATFORMS:
        raise ValueError("Unknown messenger platform")
    if isinstance(external_user_id, bool):
        raise ValueError("Invalid external user ID")
    value = str(external_user_id).strip()
    if not value or len(value) > 128:
        raise ValueError("Invalid external user ID")
    return platform, value
