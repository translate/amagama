#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""requirements/*.txt list what pyproject.toml's dependencies do."""

import pathlib
import tomllib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Each requirements file and its pyproject.toml extra (None: the dependencies).
FILES = {"required.txt": None, "recommended.txt": "recommended", "dev.txt": "dev"}

pytestmark = pytest.mark.skipif(not (ROOT / "pyproject.toml").exists(),
                                reason="not run from a checkout")


def listed(name):
    """A requirements file's requirements, not the files it includes."""
    lines = (line.strip() for line in (ROOT / "requirements" / name).read_text().splitlines())
    return {line for line in lines if line and not line.startswith(("#", "-r"))}


@pytest.mark.parametrize(("name", "extra"), FILES.items())
def test_requirements_match_pyproject(name, extra):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = project["dependencies"] if extra is None else project["optional-dependencies"][extra]
    # dev's amagama[recommended] is recommended.txt's -r.
    assert listed(name) == {r for r in wanted if not r.startswith("amagama[")}
