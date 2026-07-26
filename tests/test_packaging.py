"""The import name must not collide with anything on PyPI.

`watchtower` on PyPI is the AWS CloudWatch logging handler, and it is popular.
Shipping a top-level package of that name meant a plain `pip install` dropped
our modules into its directory and replaced its `__init__.py` - so Watchtower
worked and the user's CloudWatch logging silently stopped. `pip check` cannot
see this: nothing tracks which distribution owns an import name.

Renaming only the distribution to watchtower-tui was not enough. The import
name is the one that collides.

pyproject is read as text rather than with tomllib, which is 3.11+ while this
package supports 3.10.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_the_import_package_is_watchtower_tui():
    assert (ROOT / "src" / "watchtower_tui").is_dir()
    assert not (ROOT / "src" / "watchtower").exists(), "a top-level watchtower package collides"


def test_the_wheel_ships_only_that_package():
    assert 'packages = ["src/watchtower_tui"]' in PYPROJECT


def test_the_distribution_name_matches():
    assert 'name = "watchtower-tui"' in PYPROJECT


def test_the_command_is_still_plain_watchtower():
    """Console scripts live in Scripts/ and do not collide with a module."""
    assert 'watchtower = "watchtower_tui.cli:main"' in PYPROJECT


def test_nothing_imports_the_old_name():
    offenders = []
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("import watchtower.", "from watchtower.")) or stripped in (
                "import watchtower",
                "from watchtower import",
            ):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"these still import the colliding name: {offenders}"


def test_the_packaged_entry_point_uses_the_new_name():
    entry = (ROOT / "packaging" / "entry.py").read_text(encoding="utf-8")
    assert "from watchtower_tui.cli import main" in entry


def test_the_spec_bundles_from_the_new_path():
    spec = (ROOT / "packaging" / "watchtower.spec").read_text(encoding="utf-8")
    assert '"watchtower_tui/tui"' in spec
    assert '"watchtower_tui/assets"' in spec


def test_nothing_uses_stdlib_newer_than_the_supported_python():
    """requires-python is >=3.10, so 3.11-only modules are out.

    Twice now something has passed locally on a much newer interpreter and then
    gone red on the 3.10 leg of CI. tomllib was the most recent.
    """
    too_new = ("tomllib", "asyncio.taskgroups")
    offenders = []
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            for module in too_new:
                if stripped.startswith((f"import {module}", f"from {module} ")):
                    offenders.append(f"{path.relative_to(ROOT)}:{number} -> {module}")
    assert not offenders, f"needs Python 3.11 or newer: {offenders}"
