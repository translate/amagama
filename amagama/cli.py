#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""amaGama's management CLI (the amagama-manage command)."""

import click
from flask.cli import FlaskGroup

from amagama.application import amagama_server_factory
from amagama.benchmark import benchmark_tmdb
from amagama.commands import cli_commands


@click.group(cls=FlaskGroup, create_app=amagama_server_factory)
def manage():
    """amaGama management commands."""


for command in cli_commands:
    manage.add_command(command)
manage.add_command(benchmark_tmdb)


if __name__ == "__main__":
    manage()
