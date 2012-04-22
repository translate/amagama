# -*- coding: utf-8 -*-
#
# Copyright 2012 Zuza Software Foundation
#
# This file is part of translate.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

"""Benchmarking commands."""

import sys
import os

from translate.storage import factory

from flask import current_app

from flaskext.script import Command, Option

class BenchmarkTMDB(Command):
    """Benchmark the application by querying for all strings in the given file.

    The source strings are queried against the database. The target strings
    are ignored, although the target language of a file, if given, can be used
    as a default.
    """

    option_list = (
        Option('--source-language', '-s', dest='slang'),
        Option('--target-language', '-t', dest='tlang'),
        Option('--min-similarity', '-m', dest='min_similarity', default=None,
               help="The minimum similarity for related strings (default: server configuration)"),
        Option('--max-candidates', '-n', dest='max_candidates', default=None,
               help="The maximum number of strings to return (default: server configuration)"),
        Option('--input', '-i', dest='filename',
               help="A file or directory to use"),
    )

    def run(self, slang, tlang, min_similarity, max_candidates, filename):
        self.source_lang = slang
        self.target_lang = tlang
        self.min_similarity = min_similarity and int(min_similarity)
        self.max_candidates = max_candidates and int(max_candidates)

        try:
            import psyco
            psyco.full()
        except Exception:
            pass

        try:
            if not filename:
                print >> sys.stderr, "Please specify a file or directory to use."
            elif not os.path.exists(filename):
                print >> sys.stderr, "cannot process %s: does not exist" % filename
            elif os.path.isdir(filename):
                self.handledir(filename)
            else:
                self.handlefile(filename)
        except KeyboardInterrupt:
            pass

    def handledir(self, dirname):
        path, name = os.path.split(dirname)
        if name in ["CVS", ".svn", "_darcs", ".git", ".hg", ".bzr"]:
            return
        entries = os.listdir(dirname)
        self.handlefiles(dirname, entries)

    def handlefiles(self, dirname, filenames):
        for filename in filenames:
            pathname = os.path.join(dirname, filename)
            if os.path.isdir(pathname):
                self.handledir(pathname)
            else:
                self.handlefile(pathname)

    def handlefile(self, filename):
        print "%s:" % filename
        try:
            store = factory.getobject(filename)
            source_lang = self.source_lang or store.getsourcelanguage()
            target_lang = self.target_lang or store.gettargetlanguage()
            min_similarity = self.min_similarity
            max_candidates = self.max_candidates

            if not source_lang or not target_lang:
                print >> sys.stderr, "Missing source or target language. Can't use %s" % filename
                return
        except Exception, e:
            print >> sys.stderr, str(e)
            return

        translate_unit = current_app.tmdb.translate_unit
        try:
            for unit in store.units:
                if unit.istranslatable():
                    translate_unit(unit.source, source_lang, target_lang, min_similarity, max_candidates)
        except Exception, e:
            print e
            raise
