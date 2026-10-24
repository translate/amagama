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

import hashlib
import logging
import os
import re
import sys

import click
from flask import current_app
from flask.cli import with_appcontext
from psycopg2 import sql

from translate.lang.data import langcode_ire
from translate.lang.identify import LanguageIdentifier
from translate.storage import factory

from amagama import poparser_compat  # noqa: F401 - patches the PO parser on import
from amagama.licences import normalise_licence
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import load_sources
from amagama.tmdb import BUILD_PREFIX, OLD_PREFIX

def ensure_source_exists():
    db_name = current_app.config.get("DB_NAME")
    if not current_app.tmdb.source_langs:
        print("No source language is configured in database %s." % db_name)
        raise SystemExit(1)


source_language_option = click.option(
    '--source-language', '-s', 'source_langs', multiple=True, required=True,
    help="Source language to use (may be repeated).",
)

build_option = click.option(
    '--build', is_flag=True,
    help="Use the build schemas (%s<lang>) instead of the live ones." % BUILD_PREFIX,
)


def use_build_schemas(build):
    if build:
        current_app.tmdb.use_schemas(BUILD_PREFIX)


@click.command('initdb')
@source_language_option
@build_option
@with_appcontext
def initdb(source_langs, build):
    """Create the database tables."""
    use_build_schemas(build)
    current_app.tmdb.init_db(source_langs)
    langs = "', '".join(source_langs)
    print("Successfully initialized the database for '%s'." % langs)


@click.command('dropdb')
@source_language_option
@build_option
@click.option('--old', is_flag=True,
              help="Drop the copies (%s<lang>) that swap_db replaced." % OLD_PREFIX)
@with_appcontext
def dropdb(source_langs, build, old):
    """Drop the database."""
    if build and old:
        raise click.UsageError("Use only one of --build and --old.")
    use_build_schemas(build)
    if old:
        current_app.tmdb.use_schemas(OLD_PREFIX)
    ensure_source_exists()
    if click.confirm("This will permanently destroy all data in the "
                     "configured database. Continue?"):
        current_app.tmdb.drop_db(source_langs)
        langs = "', '".join(source_langs)
        print("Successfully dropped the database for '%s'." % langs)


@click.command('deploy_db')
@build_option
@with_appcontext
def deploy_db(build):
    """Optimise the database for deployment."""
    use_build_schemas(build)
    ensure_source_exists()
    if not click.confirm("This will permanently alter the database. Continue?"):
        return
    for slang in current_app.tmdb.source_langs:
        print('Optimising source language "%s"...' % slang)
        current_app.tmdb.deploy(slang)
        print("Finished optimising for language %s" % slang)
    print("Successfully altered the database for deployment.")


@click.command('tmdb_stats')
@build_option
@with_appcontext
def tmdb_stats(build):
    """Print some (possibly) interesting figures about the TM DB."""
    use_build_schemas(build)
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
        cursor = current_app.tmdb.get_cursor(current_app.tmdb.schema(slang))
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
        ;""").format(sql.Literal(current_app.tmdb.schema(slang)))
        summary = current_app.tmdb.import_summary(slang)
        if summary:
            print("Imports (block, files, languages, units, skipped, last):")
        for block, files, langs, units, skipped, last in summary:
            print("%s\t%d\t%d\t%d\t%d\t%s" % (
                block or '-', files, langs, units, skipped, last.date()))
        logging.info("\nIndex sizes:")
        cursor.copy_expert(query, sys.stdout)


def spdx_expression(ctx, param, value):
    if value is None:
        return None
    try:
        return normalise_licence(value)
    except ValueError as e:
        raise click.BadParameter(str(e))


def file_checksum(filename):
    digest = hashlib.sha256()
    with open(filename, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


class BuildTMDB(object):
    """Populate Translation Memory database from bilingual translation files"""

    def __init__(self, block=None, licence=None):
        self.block = block
        self.licence = licence

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

        for source_lang in current_app.tmdb.source_langs:
            current_app.tmdb.init_imports(source_lang)

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
            units = current_app.tmdb.add_store(store, source_lang, target_lang,
                                               project_style, commit=False)
            skipped = sum(1 for u in store.units if u.istranslatable()) - units
            current_app.tmdb.record_import(
                source_lang, target_lang, filename, file_checksum(filename),
                units, skipped, self.block, self.licence)
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
@click.option('--block', default=None,
              help="Name the set these files belong to, e.g. gnome-51")
@click.option('--licence', default=None, callback=spdx_expression,
              help="SPDX licence expression of the files, e.g. GPL-2.0-or-later")
@build_option
@with_appcontext
def build_tmdb(slang, tlang, project_style, filename, profile_name, verbose,
               block, licence, build):
    """Populate Translation Memory database from bilingual translation files"""
    use_build_schemas(build)
    BuildTMDB(block, licence).run(slang, tlang, project_style, filename, profile_name, verbose)


@click.command('migrate_db')
@with_appcontext
def migrate_db():
    """Copy an old database (sources_<lang> tables) into per-language schemas."""
    tmdb = current_app.tmdb
    langs = tmdb.old_layout_langs()
    if not langs:
        print("No old sources_<lang> tables found.")
        return
    for slang in langs:
        if slang in tmdb.source_langs:
            print("Skipping '%s': already migrated." % slang)
            continue
        sources, targets = tmdb.migrate_old_layout(slang)
        print("Copied '%s': %d sources, %d targets." % (slang, sources, targets))
    print("The old tables are unchanged. Run deploy_db to build the search indexes.")


@click.command('repair_mojibake')
@click.option('--apply', is_flag=True,
              help="Write the fixes. Without it, only report them.")
@with_appcontext
def repair_mojibake(apply):
    """Undo UTF-8 text that was imported as Latin-1 or Windows-1252."""
    ensure_source_exists()
    for slang in sorted(current_app.tmdb.source_langs):
        sources, targets, unconverted = current_app.tmdb.repair_mojibake(slang, apply)
        print("%s '%s': %d sources, %d targets." % (
            "Fixed" if apply else "Would fix", slang, sources, targets))
        for table, text in unconverted:
            print("  Not converted (%s): %r" % (table, text))
    if not apply:
        print("Nothing was changed. Run with --apply to write the fixes.")


def snapshot_name(ctx, param, value):
    if not re.fullmatch('[a-z][a-z0-9]*', value) or value in ('build', 'old'):
        raise click.BadParameter("use lower-case letters and digits, not build or old")
    return value


snapshot_option = click.option(
    '--name', required=True, callback=snapshot_name,
    help="Snapshot name: the schema is _<name>_<lang>, e.g. _legacy_en",
)


@click.command('snapshot_db')
@source_language_option
@snapshot_option
@with_appcontext
def snapshot_db(source_langs, name):
    """Keep a copy of the live data that each rebuild can load."""
    for slang in source_langs:
        try:
            sources, targets = current_app.tmdb.snapshot(slang, name)
        except ValueError as e:
            print(e)
            raise SystemExit(1)
        print("Saved '%s' as _%s_%s: %d sources, %d targets." % (slang, name, slang, sources, targets))


@click.command('load_snapshot')
@source_language_option
@snapshot_option
@click.option('--block', default=None, help="Name to record the snapshot under, e.g. legacy-2019")
@click.option('--licence', default=None, callback=spdx_expression,
              help="SPDX licence expression of the snapshot")
@with_appcontext
def load_snapshot(source_langs, name, block, licence):
    """Copy a snapshot into the empty build schemas."""
    current_app.tmdb.use_schemas(BUILD_PREFIX)
    for slang in source_langs:
        try:
            counts = current_app.tmdb.load_snapshot(slang, name, block, licence)
        except ValueError as e:
            print(e)
            raise SystemExit(1)
        print("Loaded _%s_%s into %s%s: %d sources (%d skipped), %d targets (%d skipped)."
              % ((name, slang, BUILD_PREFIX, slang) + counts))


@click.command('fetch_sources')
@click.option('--block', 'block_names', multiple=True,
              help="Only this block (may be repeated).")
@click.option('--check', is_flag=True,
              help="Only list the blocks that changed since they were last fetched.")
@with_appcontext
def fetch_sources(block_names, check):
    """Download the translations in the sources file."""
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    try:
        blocks = load_sources(current_app.config.get('UPDATER_SOURCES'))
    except (OSError, ValueError) as e:
        print(e)
        raise SystemExit(1)
    unknown = set(block_names) - {block.name for block in blocks}
    if unknown:
        raise click.UsageError("No block named %s." % ", ".join(sorted(unknown)))
    if block_names:
        blocks = [block for block in blocks if block.name in block_names]
    failed = False
    for block in blocks:
        try:
            if check:
                if block.changed(cache_dir):
                    print(block.name)
            else:
                state = block.fetch(cache_dir)
                print("%s: %d files, %d languages." % (
                    block.name, len(state['files']), len({lang for _, lang in state['files']})))
        except FetchError as e:
            print("%s: failed: %s" % (block.name, e))
            failed = True
    if failed:
        raise SystemExit(1)


@click.command('update')
@source_language_option
@click.option('--force', is_flag=True, help="Rebuild even if no source changed.")
@with_appcontext
def update(source_langs, force):
    """Fetch the sources, rebuild from them and swap the result in."""
    from amagama.updater.pipeline import update as update_from_sources
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    try:
        for slang in source_langs:
            update_from_sources(slang, cache_dir, current_app.config.get('UPDATER_SOURCES'),
                                current_app.config.get('UPDATER_WORKERS', 4), force)
    except (OSError, ValueError, FetchError) as e:
        print(e)
        raise SystemExit(1)


@click.command('swap_db')
@source_language_option
@click.option('--rollback', is_flag=True,
              help="Make the replaced copy live again.")
@with_appcontext
def swap_db(source_langs, rollback):
    """Make the build schemas live, keeping the replaced ones."""
    for slang in source_langs:
        try:
            current_app.tmdb.swap(slang, rollback)
        except ValueError as e:
            print(e)
            raise SystemExit(1)
        if rollback:
            print("Rolled back '%s'; the replaced copy is %s%s again." % (slang, BUILD_PREFIX, slang))
        else:
            print("Swapped in '%s'; the replaced copy is %s%s." % (slang, OLD_PREFIX, slang))


cli_commands = (
    initdb,
    dropdb,
    deploy_db,
    migrate_db,
    repair_mojibake,
    tmdb_stats,
    build_tmdb,
    snapshot_db,
    load_snapshot,
    fetch_sources,
    update,
    swap_db,
)
