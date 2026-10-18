from typing import Any


def analyze_compatibility(versions: list[dict[str, Any]], current_fields: list[str],
                          proposed_fields: list[str], endpoint: str, description: str) -> dict[str, Any]:
    """Simple deterministic schema-diff MVP. A field removed from a version's
    expected fields is considered a potential breaking change for that version.
    """
    current = set(current_fields)
    proposed = set(proposed_fields)
    removed = sorted(current - proposed)
    added = sorted(proposed - current)
    results = []

    for version in versions:
        expected = set(version.get("expected_fields", []))
        affected = sorted(expected.intersection(removed))
        results.append({
            "version": version.get("version", "unknown"),
            "platform": version.get("platform", "unknown"),
            "breaking": bool(affected),
            "affected_fields": affected,
            "active_users": version.get("active_users", 0),
            "severity": "high" if affected else "none",
            "recommendation": (
                "Keep the old field temporarily, add a compatibility mapping, or migrate this app version before removal."
                if affected else "No breakage detected for the fields recorded for this version."
            )
        })

    affected_versions = [item for item in results if item["breaking"]]
    return {
        "endpoint": endpoint,
        "description": description,
        "removed_fields": removed,
        "added_fields": added,
        "breaking_change": bool(removed),
        "affected_version_count": len(affected_versions),
        "checked_version_count": len(results),
        "results": results,
        "limitations": "This MVP compares recorded field expectations. It does not execute production requests or infer unreported users."
    }
