#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2009-2014 Zuza Software Foundation
#
# This file is part of amaGama.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

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
