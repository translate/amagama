#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""amaGama is installed whole."""

import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(not (ROOT / "pyproject.toml").exists(),
                                reason="not run from a checkout")


def test_packaged():
    """Every package is installed: one missing from pyproject.toml fails only
    where amaGama is installed, as on the server and in Docker."""
    listed = set(tomllib.loads((ROOT / "pyproject.toml").read_text())
                 ["tool"]["setuptools"]["packages"])
    found = {".".join(path.parent.relative_to(ROOT).parts)
             for path in (ROOT / "amagama").rglob("__init__.py")}
    assert found <= listed
