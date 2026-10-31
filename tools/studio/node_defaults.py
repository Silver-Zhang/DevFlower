"""Defaults for newly created nodes only; saved graphs are never migrated."""


def profile_timeout(profile):
    value = profile.get('timeout_seconds', 300)
    if type(value) is not int:
        return 300
    return min(3600, max(10, value))
