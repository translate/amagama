#!/bin/sh

export PATH=$(pwd)/bin:/usr/local/bin:$PATH
export PYTHONPATH=$(pwd):$PYTHONPATH

amagama-manage initdb -s en
amagama-manage build_tmdb --verbose -s en -t pt -i translations/