.. _managing:

Managing amaGama
****************

.. note:: The :command:`amagama-manage` command comes with the
   :ref:`installation <installation#requirements>`.

amaGama is managed through the :command:`amagama-manage` command. Try running
it with no arguments for usage help:

.. code-block:: bash

    $ amagama-manage


The :command:`amagama-manage` command exposes several management subcommands,
each having it's own :option:`--help` option that displays its usage
information:

.. code-block:: bash

    $ amagama-manage SUBCOMMAND --help


See below for the available subcommands.


.. _managing#available-subcommands:

Available subcommands
=====================

These are the available management subcommands for amaGama:


.. _managing#benchmark-tmdb:

benchmark_tmdb
--------------

This subcommand benchmarks the application by querying for all strings in the
given file.

.. note:: For more information please check the help of this subcommand.


.. _managing#build-tmdb:

build_tmdb
----------

This subcommand is used to import translations into amaGama from bilingual
translation files. Please refer to the :ref:`importing translations
<importing>` section for a complete usage example. With ``--build`` it imports
into the build schema (see :ref:`swap_db <managing#swap-db>`).


.. _managing#check-sources:

check_sources
-------------

This subcommand loads the :ref:`sources file <sources>` and every file it
includes, disabled blocks too, and says how many blocks there are; a block
that doesn't load is named and the command fails. With ``--remote`` it also
reads each git block's repository (its trees only, from the block's
``mirror`` if it has one) and names what's missing: the ref, the template, or
files a mask matches, so a block that would fail is found before it's
fetched. ``--block`` and ``--tag`` check only those blocks:

.. code-block:: console

    $ amagama-manage check_sources --remote --tag discovered
    2000 blocks, 50 disabled.
    linguas-app: filemask po/{lang}.po matches nothing
    200 git blocks checked, 1 with problems.


.. _managing#deploy-db:

deploy_db
---------

This subcommand is used to optimize the database for deployment, or with
``--build`` the build schemas:

.. code-block:: bash

    $ amagama-manage deploy_db
    This will permanently alter the database. Continue? [n] y
    Successfully altered the database for deployment.

.. note:: This is an irreversible step that alters the database permanently.

.. _managing#dropdb:

dropdb
------

This subcommand is used to drop the tables for one or more source languages
from the amaGama database:

.. code-block:: bash

    $ amagama-manage dropdb -s fr -s de
    This will permanently destroy all data in the configured database. Continue? [n] y
    Successfully dropped the database for 'fr', 'de'.

With ``--build`` it drops the build schemas instead, and with ``--old`` the
copies that :ref:`swap_db <managing#swap-db>` replaced.


.. _managing#fetch-sources:

fetch_sources
-------------

This subcommand downloads the blocks in the :ref:`sources file <sources>`
that have changed since they were last fetched (see ``min_age_days`` and
``max_age_days``) into :setting:`UPDATER_CACHE_DIR`, or with ``--block`` the
named ones whether or not they have:

.. code-block:: bash

    $ amagama-manage fetch_sources --block gnome-51
    gnome-51: 6908 files, 167 languages.

Templates (``.pot``) are not kept. A block's previous download is replaced
only once its new one is complete; a failure names the block (with how many
runs in a row it has failed, from 4) and leaves the previous download in
place. With ``--check`` it downloads nothing and lists
the blocks that changed since they were last fetched.

An empty cache fills over several runs: each run fetches at most
:setting:`UPDATER_FIRST_FETCHES` blocks for the first time, picked at random,
so each upstream sees a little of its share each run rather than all of it at
once. ``--block`` fetches the named blocks whatever the limit.

Only the blocks :setting:`UPDATER_TAGS` and :setting:`UPDATER_EXCLUDE_TAGS`
select are fetched; ``--tag`` (repeatable) narrows them to those with one of
its :ref:`tags <sources>`.


.. _managing#initdb:

initdb
------

This subcommand is used to create the tables in the database for one or several
source languages. It can be run several times to specify additional source
languages. The following example creates the tables for English and French:

.. code-block:: bash

    $ amagama-manage initdb -s en -s fr
    Successfully initialized the database for 'en', 'fr'.

With ``--build`` it creates the build schemas instead.


.. _managing#load-snapshot:

load_snapshot
-------------

This subcommand copies a snapshot made by :ref:`snapshot_db
<managing#snapshot-db>` into an empty build schema, before any other import, so
that a rebuild keeps data that can't be imported from files again:

.. code-block:: bash

    $ amagama-manage initdb -s en --build
    $ amagama-manage load_snapshot -s en --name legacy --block legacy-2019
    Loaded _legacy_en into _build_en: 1000 sources (2 skipped), 5000 targets (3 skipped).

Source ids are kept. Text that an import would also refuse is skipped: sources
longer than ``MAX_LENGTH``, and text too long for the database indexes. Each
target language is recorded as an :ref:`import <importing#record>` under the
given ``--block`` and optional ``--licence``.


.. _managing#migrate-db:

migrate_db
----------

This subcommand copies a database made by older amaGama releases, which keep
each source language in ``public.sources_<lang>`` and ``public.targets_<lang>``
tables, into the per-language schemas current amaGama uses. It has no options:

.. code-block:: bash

    $ amagama-manage migrate_db
    Copied 'en': 782372 sources, 21101176 targets.
    The old tables are unchanged. Run deploy_db to build the search indexes.

Each source language is copied in one transaction, and a language that is
already migrated is skipped, so the command can be run again. The old tables
are left in place, so the old code keeps working until you switch over; drop
them once the new version is in use. Run :ref:`deploy_db <managing#deploy-db>`
afterwards.


.. _managing#repair-mojibake:

repair_mojibake
---------------

This subcommand finds text that was UTF-8 decoded as Latin-1 or Windows-1252
before import (``LÃªer`` for ``Lêer``) and converts it back. Without options
it only reports what it would fix:

.. code-block:: bash

    $ amagama-manage repair_mojibake
    Would fix 'en': 4 sources, 1420 targets.
    Nothing was changed. Run with --apply to write the fixes.

With ``--apply`` it writes the fixes, one transaction per source
language: a broken target beside a correct copy is removed, other broken
targets and sources are replaced, and a source whose fixed text already exists
is merged into it. Text is only changed when all of it converts, and text that
looks mangled but doesn't convert is listed and left alone.


.. _managing#snapshot-db:

snapshot_db
-----------

This subcommand copies a source language's live data into ``_<name>_<lang>``,
which isn't served, for :ref:`load_snapshot <managing#load-snapshot>` to load
into each rebuild:

.. code-block:: bash

    $ amagama-manage snapshot_db -s en --name legacy
    Saved 'en' as _legacy_en: 782372 sources, 21101176 targets.

The name is lower-case letters and digits; ``build`` and ``old`` are reserved.


.. _managing#sources-status:

sources_status
--------------

This subcommand shows each block of the :ref:`sources file <sources>`: when
it was last fetched, its files and languages, the units the last build of
``--source-language`` (default ``en``) imported from it, and below that its
failed runs in a row and why it was fetched from upstream rather than its
mirror. ``--failing``, ``--empty`` (nothing to import), ``--never-fetched``
and ``--tag`` list only those blocks, and ``--json`` prints the statuses as
JSON:

.. code-block:: console

    $ amagama-manage sources_status --failing
    fedora-app: fetched 9.0 days ago, 100 files, 50 languages, 5000 units imported
      failed 4 runs in a row: git fetch: repository not found
    1 blocks.


.. _managing#swap-db:

swap_db
-------

This subcommand replaces a source language's data with a rebuilt copy. Build
the copy with ``--build`` on :ref:`initdb <managing#initdb>`,
:ref:`build_tmdb <managing#build-tmdb>` and :ref:`deploy_db
<managing#deploy-db>`: it goes into the ``_build_<lang>`` schema, which isn't
served. Then swap it in:

.. code-block:: bash

    $ amagama-manage initdb -s en --build
    $ amagama-manage load_snapshot -s en --name legacy --block legacy-2019
    $ amagama-manage build_tmdb -s en -t af -i af.po --build
    $ amagama-manage deploy_db --build
    $ amagama-manage swap_db -s en
    Swapped in 'en'; the replaced copy is _old_en.

The live and build schemas are renamed in one transaction, so lookups switch
to the new data at once, without a restart. The replaced data stays in
``_old_<lang>``: ``swap_db --rollback`` puts it back, and ``dropdb --old``
removes it. The language list served by the API refreshes when amaGama is
restarted.


.. _managing#tmdb-stats:

tmdb_stats
----------

This subcommand is used to print out some figures about the amaGama database,
or with ``--build`` the build schemas, including a summary of the
:ref:`recorded imports <importing#record>`:

.. code-block:: bash

    $ amagama-manage tmdb_stats
    Complete database (amagama):	400 MB
    Complete size of sources_en:	234 MB
    Complete size of targets_en:	160 MB
    sources_en (table only):	85 MB
    targets_en (table only):	66 MB
    sources_en	sources_en_text_idx	83 MB
    targets_en	targets_en_unique_idx	79 MB
    sources_en	sources_en_text_unique_idx	53 MB
    targets_en	targets_en_pkey	16 MB
    sources_en	sources_en_pkey	13 MB


.. _managing#update:

update
------

This subcommand refreshes a source language from the :ref:`sources file
<sources>`. It fetches the blocks that changed and, if any did (or with
``--force``), rebuilds from every block and swaps the result in:

.. code-block:: bash

    $ amagama-manage update -s en
    Fetched gnome-51: 6908 files.
    Loaded legacy-2019: 5000 targets (3 skipped).
    Imported 120 files: 9000 units (40 skipped).
    Swapped in 'en'; the replaced copy is _old_en.

The build starts empty: snapshot blocks are loaded first, then every file of
every other block, :setting:`UPDATER_WORKERS` at a time, each with its known
target language, block name and licence. Files that can't be read are listed
and left out. A database error stops it before the swap, leaving the live data
as it was. So does an import worker dying (killed for lack of memory, say):
the error names the files being imported, and if the whole update is killed,
the next one names them. The previous copy is kept as ``_old_<lang>`` until
the next update (``swap_db --rollback`` restores it).

Like ``fetch_sources``, an update fetches at most
:setting:`UPDATER_FIRST_FETCHES` blocks for the first time; the others are left
out until a later update has fetched them.

The build holds the blocks :setting:`UPDATER_TAGS` and
:setting:`UPDATER_EXCLUDE_TAGS` select, so a server or a test instance can
build a set of them, such as only ``core``.

With ``--no-fetch`` it fetches nothing and rebuilds from the cache as it is,
if the cache has changed since the last rebuild (each rebuild records what it
read): for a scheduled update when ``fetch_sources`` runs on its own schedule.

A block that fails to fetch (its server down, its repository moved) doesn't
stop the update: it is rebuilt from its last fetch, or left out if it has
none, and listed at the end with the error. Once it has failed 4 runs in a
row the list says so and since when, a sign the block needs fixing or
removing. With any
failure the update still swaps in, but exits with status 1 so a timer can
alert.

When nothing changed it prints ``Nothing changed.`` and exits, so it can run
from a timer: :file:`deploy/` has systemd units for a daily ``fetch_sources``
and a weekly ``update`` (see :file:`deploy/README.rst`). It refuses to run while a snapshot block's snapshot is missing
and the live data holds anything the blocks don't re-import, such as a
migrated database: run :ref:`snapshot_db <managing#snapshot-db>` first.


.. _managing#weblate-sources:

weblate_sources
---------------

This subcommand writes :ref:`sources file <sources>` blocks for a Weblate
instance's projects, to review before including them. It reads the instance's
component list (a few API requests) and turns each component into a ``git``
block on its repository, with its filemask, template, format and licence;
the translations then come from the repositories, not from Weblate:

.. code-block:: bash

    $ amagama-manage weblate_sources https://translate.fedoraproject.org \
          --prefix fedora --docs 'fedora-docs-*' \
          --output amagama/updater/weblate-fedora.toml
    259 blocks.
    Skipped 174 components: glossary (terminology).
    Skipped 14 components: source not English.

Components of one repository, branch, format and licence share a block, and
many single-file components under one directory are read as that directory's
files. A project on several branches gets a block per branch, with a branch
that looks like a release as its version. ``--project`` limits it to project
slugs matching a glob, and ``--docs`` marks documentation for ``segment``:
globs of a project's slug or of ``project/component``. Left out, and counted:
glossaries, components whose source isn't English, formats amaGama doesn't
read, no-derivatives licences, repositories another block already imports,
repositories on archived hosts (pagure.io), and blocks that wouldn't load. The
file's ``[defaults]`` give every block ``source = "git"``, ``min_age_days``
from ``--min-age-days`` (default 60), a :ref:`weight <sources>` from
``--weight`` (default 1) and :ref:`tags <sources>` from ``--tag`` (repeatable,
default ``platform``; ``discovered`` for ``transifex_sources`` and
``discover_sources``).

A block whose repository is on a community project's host (gitlab.gnome.org,
invent.kde.org, salsa.debian.org, Codeberg and the like) gets the project's
own GitHub :ref:`mirror <sources>` if it has one (GNOME, KDE, Xfce) or one
``discover_sources`` read it from.

Weblate's data can be out of date, so each block is then checked against its
repository, from a clone of its file list without the files: a branch that's
gone falls back to the default branch, masks that match nothing are dropped,
and a block left with none, or whose template is missing, is left out.
``--no-verify`` skips the check.

A block of the file being regenerated that is ``disabled`` (see
:ref:`sources file <sources>`) stays disabled with its reason, and one not
found again is kept as it was, so a block left out for a reason doesn't come
back. This holds for every command that writes blocks.


.. _managing#transifex-sources:

transifex_sources
-----------------

This subcommand writes :ref:`sources file <sources>` blocks for repositories
translated on Transifex, to review before including them. It clones each
repository's default branch without its files, reads every
:file:`.tx/config` in it, and turns its resources into ``git`` blocks:
``file_filter`` gives the files (``<lang>`` is the language), ``source_file``
the template of a monolingual format, ``type`` the format, and ``lang_map``
the block's ``languages``. The repositories come as arguments, or one per
line from ``--from-file``:

.. code-block:: console

    $ amagama-manage transifex_sources --prefix transifex \
          https://github.com/mapeditor/tiled https://github.com/BOINC/boinc \
          https://github.com/OpenRefine/OpenRefine --output transifex.toml
    3 blocks.
    Skipped 1 repositories: imported by another block.

A repository's resources of one format share a block; a monolingual
resource has its own, for its template. A source file its own filter matches
(:file:`tiled_en.ts`) is excluded. Left out, and counted: repositories another
block already imports, or without a :file:`.tx/config` (vendored copies,
under :file:`vendor/`, :file:`node_modules/` and the like, don't count); resources whose
source isn't English, of a type amaGama doesn't read, or whose files aren't
in the repository. ``--prefix``, ``--output``, ``--min-age-days``,
``--weight`` and ``--tag`` are as for :ref:`weblate_sources <managing#weblate-sources>`.
Transifex doesn't give a licence: add each block's before including it.


.. _managing#discover-sources:

discover_sources
----------------

This subcommand finds repositories that commit their translations, by the
localisation configuration they commit, and writes :ref:`sources file
<sources>` blocks for them, to review before including them. Its argument is
the signal; ``linguas`` is gettext's :file:`po/LINGUAS`. GitHub's code search
finds the files; it needs a token, in ``GITHUB_TOKEN``:

.. code-block:: console

    $ GITHUB_TOKEN=$(gh auth token) amagama-manage discover_sources linguas \
          --prefix linguas --weight 0.8 --output amagama/updater/linguas-github.toml

Code search allows 10 requests a minute and gives at most 1,000 results a
query, so the query is split by file size until each part is under that; a
part that can't be split further is reported as incomplete. Each
repository's stats then select it: not archived or a fork, at least
``--min-stars`` stars (default 100), pushed since ``--pushed-since`` (default
1 January of last year), and a licence GitHub detected, which becomes the
block's. GitHub gives ``GPL-3.0`` and its like, which SPDX defines as
``GPL-3.0-only``: correct a block whose project allows later versions.

A project's mirror on GitHub (GNOME's, Xfce's) is read from GitHub, but its
block fetches from the repository it copies: a repository is taken as a
mirror when its description says "mirror" or its issues are off, and its
description links a repository of the same name elsewhere; or when its
description says "mirror" and its homepage is that repository. Mirrors are
read first, as the originals of copies with more stars.

The repositories are read most stars first, each from a clone of its file
list without the files. For ``linguas``, each :file:`po/LINGUAS` (but under
:file:`vendor/`, :file:`node_modules/`, :file:`wp-content/`,
:file:`subprojects/` or :file:`third_party/`) gives a block of the
:file:`po/{lang}.po` beside it; its languages are the files there. Its
:file:`.po` files are checked out and counted: a directory with fewer than
``--min-units`` translated units (default 100), not fuzzy, is left out, as is
one with the same POT (else the same :file:`.po` files) as a repository read
before it, a copy. A repository's second :file:`po` directory is named by its
path. Repositories another block already imports are left out, as for
:ref:`weblate_sources <managing#weblate-sources>`, and so are ``--prefix``,
``--output``, ``--min-age-days``, ``--weight`` and ``--tag``.
