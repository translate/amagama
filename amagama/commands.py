#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2011 Zuza Software Foundation
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

import sys
import os

from translate.storage import factory

from flask import current_app

from flaskext.script import Command, Option

class InitDB(Command):
    """create database tables"""
    option_list = (
        Option('--source-language', '-s', dest='source_langs', action='append'),
    )

    def run(self, source_langs):
        current_app.tmdb.init_db(source_langs)

class BuildTMDB(Command):
    """Populate Translation Memory database from bilinugual translation files"""

    option_list = (
        Option('--source-language', '-s', dest='slang'),
        Option('--target-language', '-t', dest='tlang'),
        Option('--input', '-i', dest='filename'),
    )

    def run(self, slang, tlang, filename):
        self.source_lang = slang
        self.target_lang = tlang

        if not os.path.exists(filename):
            print >> sys.stderr, "cannot process %s: does not exist" % filename
        elif os.path.isdir(filename):
            self.handledir(filename)
        else:
            self.handlefile(filename)
        current_app.tmdb.connection.commit()

    def handlefile(self, filename):
        print "Importing strings from:", filename
        try:
            store = factory.getobject(filename)
            source_lang = self.source_lang or store.getsourcelanguage()
            target_lang = self.target_lang or store.gettargetlanguage()

            if not source_lang or not target_lang:
                print >> sys.stderr, "Missing source or target language. Won't import", filename
                return
        except Exception, e:
            print >> sys.stderr, str(e)
            return
        # do something useful with the store and db
        try:
            current_app.tmdb.add_store(store, source_lang, target_lang, commit=False)
        except Exception, e:
            print e
            raise

    def handlefiles(self, dirname, filenames):
        for filename in filenames:
            pathname = os.path.join(dirname, filename)
            if os.path.isdir(pathname):
                self.handledir(pathname)
            else:
                self.handlefile(pathname)

    def handledir(self, dirname):
        path, name = os.path.split(dirname)
        if name in ["CVS", ".svn", "_darcs", ".git", ".hg", ".bzr"]:
            return
        entries = os.listdir(dirname)
        self.handlefiles(dirname, entries)
 
