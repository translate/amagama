#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2008-2014 Zuza Software Foundation
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

"""A small pure Python WSGI server for development/small-scale use.

For production use, run amagama.application.amagama_server_factory() under
a real WSGI server (gunicorn, uWSGI, mod_wsgi, ...) via wsgi.py instead.
"""

import logging
from argparse import ArgumentParser

from amagama.application import amagama_server_factory


def launch_server(host, port, application):
    """Launch the given WSGI application with the best available pure
    Python WSGI server."""
    try:
        from cheroot.wsgi import Server as WSGIServer
    except ImportError:
        # cheroot isn't installed, fall back to Werkzeug's built-in
        # development server (always available, since Flask depends on it).
        from werkzeug.serving import run_simple
        run_simple(host, port, application)
        return

    server = WSGIServer((host, port), application)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


def main():
    parser = ArgumentParser()
    parser.add_argument(
        "-b", "--bind",
        default="localhost",
        help="adress to bind server to (default: %(default)s)"
    )
    parser.add_argument(
        "-p", "--port",
        default=8888,
        type=int,
        help="port to listen on (default: %(default)s)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="enable debugging features"
    )

    args = parser.parse_args()

    # Setup logging.
    if args.debug:
        level = logging.DEBUG
        format = ('%(levelname)7s %(module)s.%(funcName)s:%(lineno)d: '
                  '%(message)s')
    else:
        level = logging.WARNING
        format = '%(asctime)s %(levelname)s %(message)s'

    logging.basicConfig(level=level, format=format)

    application = amagama_server_factory()
    launch_server(args.bind, args.port, application)


if __name__ == '__main__':
    main()
