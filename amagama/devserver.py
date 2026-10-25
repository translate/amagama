#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


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
        help="address to bind server to (default: %(default)s)"
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
