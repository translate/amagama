#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# copyright 2010-2014 zuza software foundation
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

import logging
import os
import sys

from flask import current_app
from flask_script import Command, Option, prompt_bool
from translate.lang.data import langcode_ire
from translate.storage import factory


class InitDB(Command):
    """Create the database tables."""
    option_list = (
        Option('--source-language', '-s', dest='source_langs',
               action='append'),
    )

    def run(self, source_langs):
        current_app.tmdb.init_db(source_langs)
        langs = "', '".join(source_langs)
        print("Succesfully initialized the database for '%s'." % langs)


class DropDB(Command):
    """Drop the database."""
    option_list = (
        Option('--source-language', '-s', dest='source_langs',
               action='append'),
    )

    def run(self, source_langs):
        if prompt_bool("This will permanently destroy all data in the "
                       "configured database. Continue?"):
            current_app.tmdb.drop_db(source_langs)
            langs = "', '".join(source_langs)
            print("Succesfully dropped the database for '%s'." % langs)


class DeployDB(Command):
    """Optimise the database for deployment."""

    def run(self):
        if prompt_bool("This will permanently alter the database. Continue?"):
            tmdb = current_app.tmdb
            cursor = tmdb.get_cursor()
            cursor.execute(tmdb.DEPLOY_QUERY % {'slang': 'en'})
            tmdb.connection.commit()
            print("Succesfully altered the database for deployment.")


class TMDBStats(Command):
    """Print some (possibly) interesting figures about the TM DB."""

    def run(self):
        cursor = current_app.tmdb.get_cursor()
        db_name = current_app.config.get("DB_NAME")
        query = """SELECT
            pg_size_pretty(pg_database_size(%s)),
            pg_size_pretty(pg_total_relation_size('sources_en')),
            pg_size_pretty(pg_total_relation_size('targets_en')),
            pg_size_pretty(pg_relation_size('sources_en')),
            pg_size_pretty(pg_relation_size('targets_en'))
        ;"""
        data = (
            db_name,
        )
        cursor.execute(query, data)

        result = cursor.fetchone()
        print("Complete database (%s):\t%s" % (db_name, result[0]))
        print("Complete size of sources_en:\t%s" % result[1])
        print("Complete size of targets_en:\t%s" % result[2])
        print("sources_en (table only):\t%s" % result[3])
        print("targets_en (table only):\t%s" % result[4])

        # On postgres 8.3 the casts below are required. They are not needed for
        # postgres 8.4.
        query = """COPY (
            SELECT relname,
                   indexrelname,
                   pg_size_pretty(pg_relation_size(CAST(indexrelname as text)))
            FROM pg_stat_all_indexes
            WHERE schemaname = 'public'
            ORDER BY pg_relation_size(CAST(indexrelname as text)) DESC
        ) TO STDOUT
        ;"""
        logging.info("\nIndex sizes:")
        cursor.copy_expert(query, sys.stdout)


class BuildTMDB(Command):
    """Populate Translation Memory database from bilingual translation files"""

    option_list = (
        Option('--source-language', '-s', dest='slang'),
        Option('--target-language', '-t', dest='tlang'),
        Option('--project-style', dest='project_style'),
        Option('--input', '-i', dest='filename'),
        Option('--profile', '-p', dest='profile_name'),
        Option('--verbose', action='store_true', dest='verbose'),
    )

    def run(self, slang, tlang, project_style, filename, profile_name, verbose):
        """Wrapper to implement profiling if requested."""
        if profile_name:
            import cProfile
            from translate.misc.profiling import KCacheGrind
            profiler = cProfile.Profile()
            profiler.runcall(self.real_run, slang, tlang, project_style,
                             filename, verbose)
            profile_file = open(profile_name, 'w+')
            KCacheGrind(profiler).output(profile_file)
            profile_file.close()
        else:
            self.real_run(slang, tlang, project_style, filename, verbose)

    def real_run(self, slang, tlang, project_style, filename, verbose):
        self.source_lang = slang
        self.target_lang = tlang
        self.project_style = project_style

        # A simple local cache to help speed up imports
        from werkzeug.contrib.cache import SimpleCache
        current_app.cache = SimpleCache(threshold=100000)

        if not os.path.exists(filename):
            logging.error("Cannot process %s: does not exist" % filename)
        elif os.path.isdir(filename):
            self.handledir(filename, verbose)
        else:
            self.handlefile(filename, verbose)

        if verbose:
            print("Succesfully imported %s" % filename)

    def handlefile(self, filename, verbose):
        if verbose:
            print("Importing %s" % filename)

        try:
            store = factory.getobject(filename)
            source_lang = self.source_lang or store.getsourcelanguage()
            target_lang = self.target_lang or store.gettargetlanguage()
            project_style = self.project_style or store.getprojectstyle()

            if not target_lang:
                short = os.path.splitext(os.path.split(filename)[1])[0]
                if langcode_ire.match(short):
                    target_lang = short
                else:
                    short = os.path.split(os.path.split(filename)[0])[1]
                    if langcode_ire.match(short) and short != 'po':
                        target_lang = short

            if not source_lang or not target_lang:
                logging.error("Missing source or target language. Won't "
                              "import %s" % filename)
                return
        except ValueError as e:
            if not "Unknown filetype" in str(e):
                logging.exception("Error while handling: %s" % filename)
            return
        except Exception:
            logging.exception("Error while processing: %s" % filename)
            return

        # Do something useful with the store and the database.
        try:
            logging.info("Importing strings from: %s" % filename)
            current_app.tmdb.add_store(store, source_lang, target_lang,
                                       project_style, commit=True)
        except Exception:
            logging.exception("Error importing strings from: %s" % filename)
            raise

    def handlefiles(self, dirname, filenames, verbose):
        for filename in filenames:
            pathname = os.path.join(dirname, filename)
            if os.path.isdir(pathname):
                self.handledir(pathname, verbose)
            else:
                self.handlefile(pathname, verbose)

    def handledir(self, dirname, verbose):
        path, name = os.path.split(dirname)
        if name in ["CVS", ".svn", "_darcs", ".git", ".hg", ".bzr"]:
            return
        entries = os.listdir(dirname)
        self.handlefiles(dirname, entries, verbose)
