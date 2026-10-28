#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import datetime
import hashlib
import itertools
import json
import logging
import os
import re
import shutil
import sys

import click
from flask import current_app
from flask.cli import with_appcontext
from psycopg2 import sql

from translate.lang.data import langcode_ire
from translate.lang.identify import LanguageIdentifier
from translate.storage import factory

from amagama.licences import normalise_licence
from amagama.source_options import MAX_WEIGHT
from amagama.updater import discovery, generate, status, transifex, weblate
from amagama.updater.fetchers import FetchError, remembered_commits
from amagama.updater.formats import open_pieces
from amagama.updater.sources import (FAILURES_TO_REPORT, REPOSITORIES, TAGS, limit_first_fetches,
                                     load_sources, select, unused_repositories)
from amagama.tmdb import BUILD_PREFIX, OLD_PREFIX, lang_to_table

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
              help="Drop the copies (%s<lang>) that swap_db replaced; none is fine." % OLD_PREFIX)
@click.option('--yes', is_flag=True, help="Don't ask for confirmation.")
@with_appcontext
def dropdb(source_langs, build, old, yes):
    """Drop the database."""
    if build and old:
        raise click.UsageError("Use only one of --build and --old.")
    use_build_schemas(build)
    if old:
        current_app.tmdb.use_schemas(OLD_PREFIX)
        # A week whose update changed nothing leaves no replaced copy.
        missing = [lang for lang in source_langs
                   if lang_to_table(lang) not in current_app.tmdb.source_langs]
        for lang in missing:
            print("No replaced copy of '%s' to drop." % lang)
        source_langs = [lang for lang in source_langs if lang not in missing]
        if not source_langs:
            return
    ensure_source_exists()
    if yes or click.confirm("This will permanently destroy all data in the "
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
        self.files = self.units = self.skipped = 0
        # (file, reason) for each file that couldn't be imported.
        self.failed = []

    def run(self, slang, tlang, project_style, filename, profile_name, verbose):
        """Wrapper to implement profiling if requested.

        Returns the files that couldn't be imported, with the reason.
        """
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
        return self.failed

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
            self.failed.append((filename, "does not exist"))
        elif os.path.isdir(filename):
            self.handledir(filename, verbose)
        else:
            self.handlefile(filename, verbose)

        print("Imported %d files: %d units (%d skipped)." % (self.files, self.units, self.skipped))
        if self.failed:
            print("Couldn't import %d files:" % len(self.failed))
            for path, reason in self.failed:
                print("  %s: %s" % (path, reason))

    def handlefile(self, filename, verbose):
        if verbose:
            print("Importing %s" % filename)

        try:
            stores = open_pieces(filename)
            in_pieces = stores is not None
            stores = iter(stores) if in_pieces else iter([factory.getobject(filename)])
            # The languages and style come from the first piece.
            store = next(stores, None) or factory.getobject(filename)
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
                self.failed.append((filename, "missing source or target language"))
                return
        except ValueError as e:
            if "Unknown filetype" not in str(e):
                logging.exception("Error while handling: %s", filename)
                self.failed.append((filename, "%s: %s" % (type(e).__name__, e)))
            return
        except Exception as e:
            logging.exception("Error while processing: %s", filename)
            self.failed.append((filename, "%s: %s" % (type(e).__name__, e)))
            return

        # Do something useful with the store and the database.
        try:
            logging.info("Importing strings from: %s", filename)
            iid = current_app.tmdb.start_import(source_lang, target_lang, filename,
                                                self.block, self.licence)
            units = skipped = 0
            for store in itertools.chain([store], stores):
                added = current_app.tmdb.add_store(store, source_lang, target_lang,
                                                   project_style, commit=in_pieces, iid=iid)
                units += added
                skipped += sum(1 for u in store.units if u.istranslatable()) - added
            current_app.tmdb.finish_import(source_lang, iid, units, skipped,
                                           file_checksum(filename))
            self.files += 1
            self.units += units
            self.skipped += skipped
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
    if BuildTMDB(block, licence).run(slang, tlang, project_style, filename, profile_name,
                                     verbose):
        raise SystemExit(1)


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


@click.command('check_units')
@source_language_option
@click.option('--target-language', '-t', 'target_langs', multiple=True,
              help="Target language to check (may be repeated). Default: all.")
@click.option('--examples', default=3, show_default=True, help="Examples to show per check.")
@click.option('--top', default=10, show_default=True,
              help="Languages and blocks to list per check.")
@build_option
@with_appcontext
def check_units(source_langs, target_langs, examples, top, build):
    """Report stored translations that look broken. Nothing is changed."""
    from amagama.unit_checks import CHECKS, MEASURES, check_units as check
    use_build_schemas(build)
    ensure_source_exists()
    tmdb = current_app.tmdb
    for slang in source_langs:
        report = check(tmdb.get_cursor().connection, tmdb.schema(lang_to_table(slang)),
                       [lang_to_table(lang) for lang in target_langs], examples)
        print("Checked '%s': %d targets in %d languages." % (
            slang, report.targets, len(report.languages)))
        for name in list(CHECKS) + list(MEASURES):
            failures = report.failures(name)
            print("%s: %d (%.2f%%)" % (name, failures, 100 * failures / (report.targets or 1)))
            if not failures:
                continue
            print("  Languages: " + ", ".join(
                "%s %d (%.1f%%)" % (lang, n, 100 * n / report.languages[lang])
                for lang, n in report.by_language[name].most_common(top)))
            if report.by_block[name]:
                print("  Blocks: " + ", ".join(
                    "%s %d" % item for item in report.by_block[name].most_common(top)))
            for lang, source, target, blocks in report.examples[name]:
                print("  [%s] %r -> %r%s" % (
                    lang, source, target, " (%s)" % ", ".join(blocks) if blocks else ""))


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


def configured_blocks(tags=()):
    """The sources file's blocks in the configured set, and with any of tags
    if given; exits on a sources file or tag that isn't valid."""
    try:
        blocks = select(load_sources(current_app.config.get('UPDATER_SOURCES')),
                        current_app.config.get('UPDATER_TAGS'),
                        current_app.config.get('UPDATER_EXCLUDE_TAGS', ()))
        return select(blocks, list(tags)) if tags else blocks
    except (OSError, ValueError) as e:
        print(e)
        raise SystemExit(1)


@click.command('fetch_sources')
@click.option('--block', 'block_names', multiple=True,
              help="Only this block (may be repeated).")
@click.option('--tag', 'tags', multiple=True, type=click.Choice(TAGS),
              help="Only blocks with this tag (may be repeated).")
@click.option('--check', is_flag=True,
              help="Only list the blocks that changed since they were last fetched.")
@with_appcontext
@remembered_commits()
def fetch_sources(block_names, tags, check):
    """Download the translations in the sources file.

    After fetching every block, the shared git repositories no block of the
    sources file uses are removed.
    """
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    blocks = configured_blocks(tags)
    unknown = set(block_names) - {block.name for block in blocks}
    if unknown:
        raise click.UsageError("No block named %s." % ", ".join(sorted(unknown)))
    if block_names:
        blocks = [block for block in blocks if block.name in block_names]
    else:
        blocks, later = limit_first_fetches(
            blocks, cache_dir, current_app.config.get('UPDATER_FIRST_FETCHES'))
        if later:
            print("Blocks not fetched yet, left for later runs: %d." % later)
    failed = False
    unchanged = 0
    for block in blocks:
        try:
            if check:
                if block.changed(cache_dir):
                    print(block.name)
            elif not block_names and not block.changed(cache_dir):
                unchanged += 1
            else:
                state = block.fetch(cache_dir)
                print("%s: %d files, %d languages." % (
                    block.name, len(state['files']), len({entry[1] for entry in state['files']})))
                for code, sources in state['merged_languages'].items():
                    print("%s: codes merged: %s -> %s" % (block.name, ", ".join(sources), code))
                if state['unknown_languages']:
                    print("%s: not language codes, skipped: %s" % (
                        block.name, ", ".join(state['unknown_languages'])))
                if state.get('outside_links'):
                    print("%s: symlinks leading outside the block, skipped: %s" % (
                        block.name, ", ".join(state['outside_links'])))
                if block.mirror_note(cache_dir):
                    print("%s: %s" % (block.name, block.mirror_note(cache_dir)))
        except FetchError as e:
            failures = block.record_failure(cache_dir, e)
            often = (" (%d runs in a row)" % failures["runs"]
                     if failures["runs"] >= FAILURES_TO_REPORT else "")
            print("%s: failed%s: %s" % (block.name, often, e))
            failed = True
        else:
            if not check:
                block.clear_failures(cache_dir)
    if unchanged:
        print("Unchanged since the last fetch: %d blocks." % unchanged)
    if not (block_names or tags or check):
        unused = unused_repositories(
            load_sources(current_app.config.get('UPDATER_SOURCES'), disabled=True), cache_dir)
        for path in unused:
            shutil.rmtree(path)
        if unused:
            print("Removed %d repositories no block uses." % len(unused))
    if failed:
        raise SystemExit(1)


@click.command('sources_status')
@click.option('--source-language', '-s', 'slang', default='en', show_default=True,
              help="Source language whose last build gives the units imported.")
@click.option('--tag', 'tags', multiple=True, type=click.Choice(TAGS),
              help="Only blocks with this tag (may be repeated).")
@click.option('--failing', is_flag=True, help="Only blocks whose last fetch failed.")
@click.option('--empty', is_flag=True, help="Only blocks fetched or built with nothing to import.")
@click.option('--never-fetched', is_flag=True, help="Only blocks not fetched yet.")
@click.option('--json', 'as_json', is_flag=True, help="Print JSON, a list of block statuses.")
@with_appcontext
def sources_status(slang, tags, failing, empty, never_fetched, as_json):
    """Show how each block is doing: its last fetch, failures and last build."""
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    blocks = configured_blocks(tags)
    try:
        imported = {row[0]: row[3] or 0 for row in current_app.tmdb.import_summary(slang)}
    except Exception as e:
        click.echo("Units imported not known: %s" % e, err=True)
        imported = {}
    statuses = [status.block_status(block, cache_dir, imported) for block in blocks]
    if failing:
        statuses = [s for s in statuses if s["failures"]]
    if empty:
        statuses = [s for s in statuses if status.is_empty(s)]
    if never_fetched:
        statuses = [s for s in statuses if s["fetched"] is None]
    if as_json:
        click.echo(json.dumps(statuses, indent=1))
        return
    for one in statuses:
        for line in status.describe(one):
            click.echo(line)
    click.echo("%d blocks." % len(statuses), err=True)


@click.command('check_sources')
@click.option('--remote', is_flag=True,
              help="Also check that each git block's repository has its ref and files.")
@click.option('--block', 'block_names', multiple=True,
              help="Only this block (may be repeated), with --remote.")
@click.option('--tag', 'tags', multiple=True, type=click.Choice(TAGS),
              help="Only blocks with this tag (may be repeated), with --remote.")
@with_appcontext
def check_sources(remote, block_names, tags):
    """Check the sources file, and with --remote what its git blocks read."""
    try:
        every = load_sources(current_app.config.get('UPDATER_SOURCES'), disabled=True)
    except (OSError, ValueError) as e:
        print(e)
        raise SystemExit(1)
    print("%d blocks, %d disabled." % (len(every), sum(1 for b in every if not b.enabled)))
    if not remote:
        return
    blocks = configured_blocks(tags)
    if block_names:
        unknown = set(block_names) - {block.name for block in blocks}
        if unknown:
            raise click.UsageError("No block named %s." % ", ".join(sorted(unknown)))
        blocks = [block for block in blocks if block.name in block_names]
    problems = status.check_remote(blocks)
    for name, found in problems.items():
        for problem in found:
            print("%s: %s" % (name, problem))
    print("%d git blocks checked, %d with problems." % (
        sum(1 for block in blocks if block.source == "git"), len(problems)))
    if problems:
        raise SystemExit(1)


@click.command('sources_pot')
@click.option('--check', is_flag=True,
              help="Exit 1 if amagama/po/sources/sources.pot isn't current; write nothing.")
def sources_pot(check):
    """Write the template for translating block titles, from amaGama's own sources file."""
    from amagama.titles import TEMPLATE, template
    try:
        text = template(load_sources())
    except (OSError, ValueError) as e:
        print(e)
        raise SystemExit(1)
    if check:
        try:
            with open(TEMPLATE, encoding='utf-8') as f:
                current = f.read() == text
        except FileNotFoundError:
            current = False
        if not current:
            print("%s is out of date: run amagama-manage sources_pot." % TEMPLATE)
            raise SystemExit(1)
        return
    with open(TEMPLATE, 'w', encoding='utf-8') as f:
        f.write(text)


def generator_options(tags):
    """The options of a command writing sources-file blocks for review, tags
    being the default tags of its blocks."""
    def options(command):
        return _generator_options(command, tags)
    return options


def _generator_options(command, tags):
    for option in reversed((
            click.option('--prefix', required=True, help="Start of each block's name, such as fedora."),
            click.option('--output', type=click.Path(dir_okay=False),
                         help="File to write; else standard output."),
            click.option('--min-age-days', type=click.IntRange(min=0), default=60, show_default=True,
                         help="Days after a fetch before each block is checked again."),
            click.option('--weight', type=click.FloatRange(0, MAX_WEIGHT), default=1,
                         show_default=True, help="Each block's default weight in lookups."),
            click.option('--tag', 'tags', multiple=True, default=tags, show_default=True,
                         type=click.Choice(TAGS), help="The blocks' tags (may be repeated)."),
            click.option('--report', type=click.Path(dir_okay=False),
                         help="Markdown file to write of the blocks new, gone and disabled since "
                         "the last run, for review."))):
        command = option(command)
    return command


def shared_repositories():
    """generate.shared_repositories() of the cache's, if there is a cache."""
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    return generate.shared_repositories(
        os.path.join(cache_dir, REPOSITORIES) if os.path.isdir(cache_dir) else None)


def with_shared_repositories(command):
    """command, run with shared_repositories()."""
    @functools.wraps(command)
    def wrapped(*args, **kwargs):
        with shared_repositories():
            return command(*args, **kwargs)
    return wrapped


def generated_context(prefix):
    """generate.context() of the sources file, for writing blocks whose
    names start with prefix."""
    try:
        existing = load_sources(current_app.config.get('UPDATER_SOURCES'), disabled=True)
    except (OSError, ValueError):
        existing = []
    return generate.context(existing, prefix)


def generated_repositories(prefix):
    """The GitHub repositories ("owner/name") of the blocks whose names start
    with prefix: each block's own, or its mirror's."""
    try:
        existing = load_sources(current_app.config.get('UPDATER_SOURCES'), disabled=True)
    except (OSError, ValueError):
        return set()
    found = set()
    for block in existing:
        if block.name.startswith(prefix + "-") and block.source == "git":
            settings = block.fetcher.settings
            name = discovery.github_name(settings.get("url")) or \
                discovery.github_name(settings.get("mirror"))
            if name:
                found.add(name)
    return found


def write_generated(found, skipped, command, min_age_days, weight, tags, output, disabled=None,
                    names=None, report=None):
    """Write found blocks for review, then report them and skipped:
    {(what, reason): count}. A block named in disabled stays disabled, with
    its reason; one not found again is kept as it was, so it doesn't come
    back enabled when it is. names is the generate.Names of the last run's
    blocks; report a Markdown file to write of what changed since then."""
    header = ("Generated by: amagama-manage %s --min-age-days %d%s%s\n"
              "Review before use: drop what amaGama shouldn't import.") % (
        command, min_age_days, " --weight %g" % weight if weight != 1 else "",
        "".join(" --tag %s" % tag for tag in tags))
    defaults = {"source": "git"}
    if min_age_days:
        defaults["min_age_days"] = min_age_days
    if weight != 1:
        defaults["weight"] = weight
    if tags:
        defaults["tags"] = list(tags)
    disabled = disabled or {}
    found, missing = generate.with_disabled(found, disabled)
    text = generate.toml(found, header, defaults)
    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        click.echo(text, nl=False)
    click.echo("%d blocks%s%s." % (
        len(found), ", %d disabled" % len(disabled) if disabled else "",
        " (%d not found again)" % missing if missing else ""), err=True)
    previous = set(names.previous.values()) if names else set()
    new, gone = generate.changes(found, previous, disabled)
    click.echo("%d new and %d gone since the last run." % (len(new), len(gone)), err=True)
    if report:
        with open(report, "w", encoding="utf-8") as f:
            f.write(generate.report(found, previous, disabled, command))
    for (what, reason), count in sorted(skipped.items()):
        click.echo("Skipped %d %s: %s." % (count, what, reason), err=True)


@click.command('weblate_sources')
@click.argument('url')
@click.option('--project', 'projects', multiple=True, default=["*"], show_default=True,
              help="Glob of the project slugs to include; repeatable.")
@click.option('--docs', multiple=True, help="Glob of documentation, project or "
              "project/component, imported with segmentation; repeatable.")
@click.option('--no-verify', is_flag=True, help="Don't check blocks against their "
              "repositories.")
@generator_options(("platform",))
@with_appcontext
@with_shared_repositories
def weblate_sources(url, projects, docs, no_verify, prefix, output, min_age_days, weight, tags,
                    report):
    """Write sources-file blocks for a Weblate instance's projects, for review.

    Repositories that blocks in the sources file already import are left out,
    but for blocks with the same prefix, which this regenerates. Each block is
    checked against its repository, and left out if it would import nothing,
    unless --no-verify.
    """
    names, covered, disabled = generated_context(prefix)
    try:
        found, skipped = weblate.blocks(weblate.components(url), prefix, projects, docs, covered,
                                        names)
    except FetchError as e:
        raise click.ClickException(str(e))
    if not no_verify:
        found, problems = generate.verify(found)
        skipped.update(problems)
        found, problems = generate.importable(found)
        skipped.update(problems)
        found = generate.add_mirrors(found)
    command = "weblate_sources %s --prefix %s%s%s" % (
        url, prefix, "".join(" --project %s" % p for p in projects if p != "*"),
        "".join(" --docs %s" % d for d in docs))
    write_generated(found, {("components", reason): count for reason, count in skipped.items()},
                    command, min_age_days, weight, tags, output, disabled, names, report)


@click.command('transifex_sources')
@click.argument('repositories', nargs=-1)
@click.option('--from-file', type=click.File(encoding="utf-8"),
              help="File of repository URLs, one per line.")
@generator_options(("discovered",))
@with_appcontext
@with_shared_repositories
def transifex_sources(repositories, from_file, prefix, output, min_age_days, weight, tags,
                      report):
    """Write sources-file blocks for repositories translated on Transifex, for review.

    Each repository's default branch is cloned without its files, and every
    .tx/config in it read. Repositories that blocks in the sources file
    already import are left out, but for blocks with the same prefix, which
    this regenerates.
    """
    command = " ".join(["transifex_sources --prefix", prefix] + list(repositories)
                       + (["--from-file", from_file.name] if from_file else []))
    repositories = list(repositories)
    if from_file:
        repositories += [line.strip() for line in from_file
                         if line.strip() and not line.startswith("#")]
    if not repositories:
        raise click.UsageError("Give repositories, or --from-file.")
    names, covered, disabled = generated_context(prefix)
    found, skipped = transifex.blocks(repositories, prefix, covered, names)
    found = generate.add_mirrors(found)
    write_generated(found, skipped, command, min_age_days, weight, tags, output, disabled,
                    names, report)


@click.command('discover_sources')
@click.argument('signal', type=click.Choice(sorted(discovery.SIGNALS)))
@click.option('--min-stars', type=click.IntRange(min=0), default=100, show_default=True,
              help="Fewest stars a repository needs.")
@click.option('--pushed-since', type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Leave out repositories not pushed since this date; else 1 January "
              "of last year.")
@click.option('--min-units', type=click.IntRange(min=0), default=100, show_default=True,
              help="Fewest translated units, not fuzzy, a block needs (linguas).")
@click.option('--search-pause', type=click.FloatRange(min=0), default=6, show_default=True,
              envvar='AMAGAMA_SEARCH_PAUSE',
              help="Seconds between code searches: GitHub allows a token 10 a minute, but "
              "a workflow's token about one.")
@click.option('--bot-prs-days', type=click.IntRange(min=0), default=0, show_default=True,
              help="Also read repositories with a translation bot's pull request merged in "
              "the last days (crowdin).")
@generator_options(("discovered",))
@with_appcontext
@with_shared_repositories
def discover_sources(signal, min_stars, pushed_since, min_units, search_pause, bot_prs_days, prefix,
                     output, min_age_days, weight, tags, report):
    """Write sources-file blocks for repositories that commit a signal's
    localisation configuration, for review.

    GitHub's code search finds them, with the token in GITHUB_TOKEN. Those not
    archived or forks, with enough stars, pushed recently and with a licence,
    are cloned without their files, mirrors then most stars first, and read.
    A mirror's block is of the repository it copies. The repositories of the
    blocks being regenerated are read too, as code search misses some each
    run. Repositories that blocks in the sources file already import are left
    out, but for blocks with the same prefix, which this regenerates.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise click.UsageError("GitHub's code search needs a token: set GITHUB_TOKEN.")
    bot_prs = discovery.SIGNALS[signal].get("bot_prs", [])
    if bot_prs_days and not bot_prs:
        raise click.UsageError("%s has no translation bot's pull requests." % signal)
    pushed_since = pushed_since.date() if pushed_since else discovery.default_pushed_since()
    try:
        names, incomplete = set(), False
        for query in discovery.SIGNALS[signal]["queries"]:
            found, missing = discovery.search(query, token, pause=search_pause)
            names, incomplete = names | set(found), incomplete or missing
        if bot_prs_days:
            since = datetime.date.today() - datetime.timedelta(days=bot_prs_days)
            merged = set()
            for query in bot_prs:
                found, missing = discovery.search_prs(query, token, since)
                merged, incomplete = merged | set(found), incomplete or missing
            click.echo("%d repositories with a bot's pull request, %d of them not found by "
                       "code search." % (len(merged), len(merged - names)), err=True)
            names |= merged
        # Code search's results differ from run to run: the blocks being
        # regenerated are read again whether it found them or not.
        previous = generated_repositories(prefix) - names
        click.echo("%d repositories found%s%s." % (
            len(names), ", some of the search's results missing" if incomplete else "",
            ", and %d more from the blocks being regenerated" % len(previous) if previous else ""),
            err=True)
        names = sorted(names | previous)
        kept, skipped = discovery.select(discovery.stats(names, token), min_stars, pushed_since)
    except FetchError as e:
        raise click.ClickException(str(e))
    # A mirror's block is its upstream's, read from the mirror; mirrors come
    # first, as originals of the copies with more stars.
    pairs = sorted(((discovery.upstream(repository) or repository["url"], repository)
                    for repository in kept), key=lambda pair: pair[0] == pair[1]["url"])
    urls = [url for url, _ in pairs]
    kept = [repository for _, repository in pairs]
    mirrors = {url: repository["url"] for url, repository in pairs if url != repository["url"]}
    if mirrors:
        click.echo("%d mirrors: their blocks use the repositories they copy." % len(mirrors),
                   err=True)
    names, covered, disabled = generated_context(prefix)
    found, resolved = discovery.SIGNALS[signal]["blocks"](
        urls, prefix, covered, names,
        licences={generate.normal_url(url): discovery.licence(repository)
                  for url, repository in zip(urls, kept)},
        min_units=min_units, mirrors=mirrors)
    found = generate.add_mirrors(found)
    skipped.update(resolved)
    command = "discover_sources %s --prefix %s --min-stars %d --pushed-since %s --min-units %d%s" % (
        signal, prefix, min_stars, pushed_since.isoformat(), min_units,
        " --bot-prs-days %d" % bot_prs_days if bot_prs_days else "")
    write_generated(found, skipped, command, min_age_days, weight, tags, output, disabled,
                    names, report)

@click.command('update')
@source_language_option
@click.option('--force', is_flag=True, help="Rebuild even if no source changed.")
@click.option('--no-fetch', 'fetch', is_flag=True, flag_value=False, default=True,
              help="Rebuild from the cache as it is, if it changed since the last rebuild; "
              "for when fetch_sources keeps it current.")
@with_appcontext
@remembered_commits()
def update(source_langs, force, fetch):
    """Fetch the sources, rebuild from them and swap the result in."""
    from amagama.updater.pipeline import update as update_from_sources
    cache_dir = os.path.expanduser(current_app.config.get('UPDATER_CACHE_DIR'))
    failed = False
    try:
        for slang in source_langs:
            failed |= bool(update_from_sources(
                slang, cache_dir, current_app.config.get('UPDATER_SOURCES'),
                current_app.config.get('UPDATER_WORKERS', 4), force,
                first_fetches=current_app.config.get('UPDATER_FIRST_FETCHES'), fetch=fetch,
                tags=current_app.config.get('UPDATER_TAGS'),
                exclude_tags=current_app.config.get('UPDATER_EXCLUDE_TAGS', ())))
    except (OSError, ValueError, FetchError) as e:
        print(e)
        raise SystemExit(1)
    if failed:
        # Rebuilt without them, but a scheduled run should alert.
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
    check_units,
    tmdb_stats,
    build_tmdb,
    snapshot_db,
    load_snapshot,
    fetch_sources,
    update,
    swap_db,
    weblate_sources,
    transifex_sources,
    discover_sources,
    sources_status,
    check_sources,
    sources_pot,
)
