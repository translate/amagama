#!/bin/bash -eu
#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

# psycopg2 builds from source; its C code is fuzzed by its own OSS-Fuzz
# project, not here.
CFLAGS="" CXXFLAGS="" pip3 install ".[recommended]"

for fuzzer in fuzz/fuzz_*.py; do
    name=$(basename "$fuzzer" .py)
    # The readers are imported by name, and the language and title data are
    # package files.
    compile_python_fuzzer "$fuzzer" \
        --collect-submodules translate.storage \
        --collect-data amagama --collect-data pycountry --collect-data translate
    (cd "fuzz/corpus/$name" && zip -q -r "$OUT/${name}_seed_corpus.zip" .)
done
