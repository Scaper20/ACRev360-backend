"""
Regression guard for docs/RBAC_EXPANSION_DESIGN.md's closed-world guarantee:
RATEPAYER/RATEPAYER_PROXY must never be passed to access_level_permission(...)
outside apps/registry/api — those two levels only ever reach a caller's own
(or delegated) data through IsRatepayerOrDelegate + accessible_payer_ids, and
must never gain a foothold on any staff-facing viewset. A future edit adding
either level to, say, BillViewSet's permission_classes by copy-paste mistake
would otherwise be a silent, serious data leak — this makes it a test
failure instead. Source-inspection, same convention as
tests/test_migration_helpers.py's guard against migration_helpers.for_each_council
being quietly bypassed.
"""
import pathlib
import re

import apps

_APPS_ROOT = pathlib.Path(apps.__file__).parent
_CALL_PATTERN = re.compile(r"access_level_permission\(([^)]*)\)", re.DOTALL)
_ALLOWED_DIR = _APPS_ROOT / "registry" / "api"


def _api_files():
    return [p for p in _APPS_ROOT.glob("*/api/*.py") if p.is_file()]


def test_ratepayer_levels_never_reach_access_level_permission_outside_registry_api():
    offenders = []
    for path in _api_files():
        if _ALLOWED_DIR in path.parents or path.parent == _ALLOWED_DIR:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _CALL_PATTERN.finditer(text):
            args = match.group(1)
            if "AppRole.RATEPAYER" in args:
                offenders.append(f"{path.relative_to(_APPS_ROOT.parent)}: {match.group(0)}")
    assert not offenders, (
        "RATEPAYER/RATEPAYER_PROXY must never be passed to access_level_permission(...) "
        f"outside apps/registry/api — found:\n" + "\n".join(offenders)
    )


def test_ratepayer_portal_uses_the_dedicated_permission_class():
    """The one place these levels ARE allowed to gate something — confirms
    IsRatepayerOrDelegate (not access_level_permission) is what's actually
    used, so the exclusion above isn't accidentally hiding a real gap."""
    views_path = _APPS_ROOT / "registry" / "api" / "views.py"
    text = views_path.read_text(encoding="utf-8")
    assert "IsRatepayerOrDelegate" in text
    assert "class RatepayerPortalViewSet" in text
