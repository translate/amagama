#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import os
import sys

# This part is only required when running from checkout instead of an install,
# and only if PYTHONPATH was not exported before running.
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT_DIR)  # Top level directory.

# Keep this import after the path altering.
import amagama.application  # noqa: E402


application = amagama.application.amagama_server_factory()
