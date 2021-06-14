#!/bin/sh

export PATH=$(pwd)/bin:/usr/local/bin:$PATH
export PYTHONPATH=$(pwd):$PYTHONPATH

amagama -b 0.0.0.0 -p 8888 --debug