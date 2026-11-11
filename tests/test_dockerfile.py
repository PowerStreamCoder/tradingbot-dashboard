"""
Deployment-shape tests (dashboard #98 follow-up).

The first attempt to ship the market-hours gate failed at the Cloud Run
"container failed to start and listen on the port" step: the Dockerfile lists
application files explicitly, and the newly vendored ``market_hours.py`` was
not among them, so ``main.py`` crashed on import inside the image while all
CI checks — which run on the checkout, not the image — were green.

These tests keep the image contents in lockstep with the repo root.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_ships_every_root_module():
    """Every root-level .py module must appear in a Dockerfile COPY line.

    ``main.py`` imports root modules by name (``access_code``,
    ``market_hours``), so a module present in the checkout but absent from
    the image only fails at container startup in Cloud Run — the exact
    failure mode of the #98 deploy.
    """
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    copied = {m.group(1) for m in re.finditer(r"^COPY\s+(\S+)", dockerfile, re.M)}

    root_modules = sorted(p.name for p in REPO_ROOT.glob("*.py"))
    assert root_modules, "expected at least main.py in the repo root"

    missing = [m for m in root_modules if m not in copied]
    assert not missing, (
        f"Dockerfile does not COPY root modules {missing} — main.py imports "
        f"by module name, so the container will crash at startup in Cloud Run. "
        f"Add a COPY line for each."
    )


def test_gcloudignore_does_not_exclude_root_modules():
    """.gcloudignore (source deploys) must not exclude root modules either."""
    ignore = (REPO_ROOT / ".gcloudignore").read_text()
    root_modules = sorted(p.name for p in REPO_ROOT.glob("*.py"))

    excluded = [
        m for m in root_modules
        if any(
            line.strip() == m or line.strip().rstrip("/") == m
            for line in ignore.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    ]
    assert not excluded, f".gcloudignore excludes root modules {excluded}"
