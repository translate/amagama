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

import click
from flask import current_app
from flask.cli import with_appcontext
from psycopg2 import sql

from translate.lang.data import langcode_ire
from translate.lang.identify import LanguageIdentifier
from translate.storage import factory

from amagama.tmdb import (
    min_levenshtein_length as min_leven,
    max_levenshtein_length as max_leven,
)

def ensure_source_exists():
    db_name = current_app.config.get("DB_NAME")
    if not current_app.tmdb.source_langs:
        print("No source language is configured in database %s." % db_name)
        raise SystemExit(1)


source_language_option = click.option(
    '--source-language', '-s', 'source_langs', multiple=True, required=True,
    help="Source language to use (may be repeated).",
)


@click.command('initdb')
@source_language_option
@with_appcontext
def initdb(source_langs):
    """Create the database tables."""
    current_app.tmdb.init_db(source_langs)
    langs = "', '".join(source_langs)
    print("Successfully initialized the database for '%s'." % langs)


@click.command('dropdb')
@source_language_option
@with_appcontext
def dropdb(source_langs):
    """Drop the database."""
    ensure_source_exists()
    if click.confirm("This will permanently destroy all data in the "
                     "configured database. Continue?"):
        current_app.tmdb.drop_db(source_langs)
        langs = "', '".join(source_langs)
        print("Successfully dropped the database for '%s'." % langs)


@click.command('deploy_db')
@with_appcontext
def deploy_db():
    """Optimise the database for deployment."""
    ensure_source_exists()
    if not click.confirm("This will permanently alter the database. Continue?"):
        return
    tmdb = current_app.tmdb
    SIMILARITY = current_app.config.get("MIN_SIMILARITY")
    MAX_LENGTH = current_app.config.get("MAX_LENGTH")
    for slang in current_app.tmdb.source_langs:
        print('Optimising source language "%s"...' % slang)
        cursor = tmdb.get_cursor(slang)
        cursor.execute(tmdb.DEPLOY_QUERY)
        upper_bounds = (28, 93)
        lower_bound = 0
        for upper_bound in upper_bounds:
            idx_name = "sources_up_to_%d_text_idx" % upper_bound
            if lower_bound == 0:
                cursor.execute(sql.SQL("""
                    CREATE INDEX {} ON sources USING gin(vector)
                    WHERE length <= %s""").format(
                        sql.Identifier(idx_name),
                    ),
                   (max_leven(upper_bound, SIMILARITY, MAX_LENGTH),))
            else:
                bounds = (min_leven(lower_bound, SIMILARITY),
                          max_leven(upper_bound, SIMILARITY, MAX_LENGTH))
                cursor.execute(sql.SQL("""
                    CREATE INDEX {} ON sources USING gin(vector)
                    WHERE length BETWEEN %s AND %s""").format(
                        sql.Identifier(idx_name)
                    ),
                    bounds
                )
            lower_bound = upper_bound + 1

        cursor.execute("""
            CREATE INDEX sources_long_idx ON sources USING gin(vector)
            WHERE length >= %s""",
                       (min_leven(lower_bound, SIMILARITY),))
        cursor.connection.commit()
        print("Finished optimising for language %s" % slang)
    print("Successfully altered the database for deployment.")


@click.command('tmdb_stats')
@with_appcontext
def tmdb_stats():
    """Print some (possibly) interesting figures about the TM DB."""
    ensure_source_exists()
    db_name = current_app.config.get("DB_NAME")

    cursor = current_app.tmdb.get_cursor()
    query = """SELECT pg_size_pretty(pg_database_size(%s))"""
    cursor.execute(query, (db_name,))
    result = cursor.fetchone()
    print("Complete database (%s):\t%s" % (db_name, result[0]))

    for slang in current_app.tmdb.source_langs:
        print()
        print("Source language:", slang)
        cursor = current_app.tmdb.get_cursor(slang)
        query = """SELECT
            pg_size_pretty(pg_total_relation_size('sources')),
            pg_size_pretty(pg_total_relation_size('targets')),
            pg_size_pretty(pg_relation_size('sources')),
            pg_size_pretty(pg_relation_size('targets'))
        ;"""
        data = (
            db_name,
        )
        cursor.execute(query, data)

        result = cursor.fetchone()
        print("Complete size of sources:\t%s" % result[0])
        print("Complete size of targets:\t%s" % result[1])
        print("sources (table only):\t%s" % result[2])
        print("targets (table only):\t%s" % result[3])

        query = sql.SQL("""COPY (
            SELECT relname,
                   indexrelname,
                   pg_size_pretty(pg_relation_size(CAST(indexrelname as text)))
            FROM pg_stat_user_indexes
            WHERE schemaname = {}
            ORDER BY pg_relation_size(CAST(indexrelname as text)) DESC
        ) TO STDOUT
        ;""").format(sql.Literal(slang))
        logging.info("\nIndex sizes:")
        cursor.copy_expert(query, sys.stdout)


class BuildTMDB(object):
    """Populate Translation Memory database from bilingual translation files"""

    def run(self, slang, tlang, project_style, filename, profile_name, verbose):
        """Wrapper to implement profiling if requested."""
        ensure_source_exists()
        if profile_name:
            import cProfile
            from amagama.profiling import KCacheGrind
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
        from flask_caching import Cache
        cache = Cache(current_app, config={
            'CACHE_TYPE': 'SimpleCache',
            'CACHE_THRESHOLD': 100000,
        })
        current_app.cache = cache

        if not os.path.exists(filename):
            logging.error("Cannot process %s: does not exist", filename)
        elif os.path.isdir(filename):
            self.handledir(filename, verbose)
        else:
            self.handlefile(filename, verbose)

        if verbose:
            print("Successfully imported %s" % filename)

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
                    long, short = os.path.split(os.path.split(filename)[0])
                    if langcode_ire.match(short) and short not in ('po', 'www', 'gtk'):
                        target_lang = short
                    else:
                        short = os.path.split(long)[1]
                        if langcode_ire.match(short) and short not in ('po', 'www', 'gtk'):
                            detected = LanguageIdentifier().identify_target_lang(store.units)
                            if short == detected:
                                target_lang = short
                                print('Guessing language "%s" for %s' % (short, filename))

            if not source_lang or not target_lang:
                logging.error("Missing source or target language. Won't "
                              "import %s", filename)
                return
        except ValueError as e:
            if "Unknown filetype" not in str(e):
                logging.exception("Error while handling: %s", filename)
            return
        except Exception:
            logging.exception("Error while processing: %s", filename)
            return

        # Do something useful with the store and the database.
        try:
            logging.info("Importing strings from: %s", filename)
            current_app.tmdb.add_store(store, source_lang, target_lang,
                                       project_style, commit=True)
        except Exception:
            logging.exception("Error importing strings from: %s", filename)
            raise

    def handlefiles(self, dirname, filenames, verbose):
        for filename in filenames:
            pathname = os.path.join(dirname, filename)
            if os.path.isdir(pathname):
                self.handledir(pathname, verbose)
            else:
                ext = os.path.splitext(pathname)[-1]
                if ext.lower() in {'.txt', '.utf8', '.pot', '.csv', '.tab'}:
                    continue
                self.handlefile(pathname, verbose)

    def handledir(self, dirname, verbose):
        path, name = os.path.split(dirname)
        if name in {"CVS", ".svn", "_darcs", ".git", ".hg", ".bzr"}:
            return
        entries = os.listdir(dirname)
        self.handlefiles(dirname, entries, verbose)


@click.command('build_tmdb')
@click.option('--source-language', '-s', 'slang', default=None)
@click.option('--target-language', '-t', 'tlang', default=None)
@click.option('--project-style', default=None)
@click.option('--input', '-i', 'filename', required=True,
             help="A file or directory to use")
@click.option('--profile', '-p', 'profile_name', default=None,
             help="Write cProfile/KCacheGrind output to this file")
@click.option('--verbose', is_flag=True, default=False)
@with_appcontext
def build_tmdb(slang, tlang, project_style, filename, profile_name, verbose):
    """Populate Translation Memory database from bilingual translation files"""
    BuildTMDB().run(slang, tlang, project_style, filename, profile_name, verbose)


cli_commands = (
    initdb,
    dropdb,
    deploy_db,
    tmdb_stats,
    build_tmdb,
)
