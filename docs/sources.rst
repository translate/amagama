.. _sources:

Translation sources
*******************

The translations amaGama imports are listed in a sources file,
:file:`amagama/updater/sources.toml` unless :setting:`UPDATER_SOURCES` names
another. Each ``[[block]]`` is a set of files fetched and imported together,
such as one GNOME release:

.. code-block:: toml

    [[block]]
    name = "gnome-51"
    title = "GNOME"
    version = "51"
    source = "damned-lies"
    release = "gnome-51"
    licence = "GPL-2.0-or-later"

``name``
  The block's fixed reference: it names the block's directory in the cache
  and its :ref:`imports <importing#record>`, so never rename or reuse one.
  Blocks are read in the order they appear in the file.

``title``
  The name shown to people, such as ``GNOME`` or ``Legacy OSS``. Titles are
  meant to be translated, so keep release numbers in ``version``.

``version``
  Optional release, such as ``"51"``; shown after the title (GNOME 51).

``source``
  How to fetch the files; the other keys depend on it (below).

``licence``
  Optional `SPDX licence expression <https://spdx.org/licenses/>`_, checked as
  for ``build_tmdb``.

``files``
  Optional filemasks choosing which of the block's files to import, relative
  to its directory. ``*`` matches within a directory, ``**`` any number of
  directories, and ``{lang}`` the target language (a code such as ``af``,
  ``pt_BR``, ``sr@latin`` or ``b+sr+Latn``), which then overrides the
  language the source gives:

  .. code-block:: toml

      files = ["addons/*/i18n/{lang}.po"]
      files = ["translations/*_{lang}.ts"]
      files = ["**/locale/{lang}/LC_MESSAGES/django.po"]

  Without it every file is imported. A block whose masks match no file
  fails, rather than loading nothing.

``exclude``
  Optional masks, without ``{lang}``, of files to leave out, such as
  ``["**/qqq.json"]``.

``keep``
  Optional masks, without ``{lang}``, of files the import needs beside the
  translations but that aren't imported themselves, such as the
  :file:`.grdp` parts a Chromium :file:`.grd` includes.

``languages``
  Optional table of a source's language codes to amaGama's, applied before
  the normalising below; map a code to ``""`` to leave it out:

  .. code-block:: toml

      languages = { no = "nb", xxx = "" }

``format``
  Optional reader for the block's files, when their extension doesn't say:
  ``po``, ``mo``, ``ts``, ``tmx``, ``tbx``, ``xliff``, ``csv`` or
  ``nextcloud-json`` (Nextcloud's ``l10n/<lang>.json``, keyed by the English
  text). Without it the extension picks the reader.

  Monolingual formats hold one language per file, keyed by an id, and need a
  ``template``: ``android`` (:file:`strings.xml`), ``php``, ``properties``,
  ``joomla-ini``, ``json`` (key to message), ``fluent`` and ``dtd``.

  ``xtb`` is Chromium's: each :file:`.xtb` translation is keyed by a
  fingerprint of the English message, which is in the :file:`.grd` named by
  ``template`` (and the :file:`.grdp` parts it includes, which ``keep`` must
  keep):

  .. code-block:: toml

      format = "xtb"
      files = ["*/strings/translations/*_{lang}.xtb"]
      template = "*/strings/*.grd"
      keep = ["**/*.grdp"]

``template``
  For a monolingual format, the English file each translation pairs with by
  id: a path with the same wildcards as ``files``, each taking what it
  matched in the translation's path:

  .. code-block:: toml

      files = ["res/values-{lang}/strings.xml"]
      template = "res/values/strings.xml"

      files = ["{lang}/*.php"]
      template = "en/*.php"

  The template is kept but not imported itself. Messages with no English,
  and plurals, are left out; a translation whose template is missing is
  listed as unreadable.

``columns`` and ``header_row``
  For ``format = "csv"``: what each column holds, from ``"source"``,
  ``"target"`` and ``"location"``, with ``""`` for a column to ignore; and
  whether the first row is a header to skip. Files are then read as plain
  comma-separated values:

  .. code-block:: toml

      format = "csv"
      columns = ["", "source", "target"]
      header_row = true

``xliff_states``
  For XLIFF files: the target states that count as translated, from XLIFF
  1.2's ``new``, ``needs-translation``, ``needs-review-translation``,
  ``translated``, ``final``, ``signed-off`` and the like, ``x-`` custom
  states, and ``"none"`` for a target without a state. A unit with
  ``approved="yes"`` always counts. Without it, ``translated``, ``final`` and
  ``signed-off`` count:

  .. code-block:: toml

      xliff_states = ["none", "translated", "final", "signed-off"]

``refetch_days``
  For a source whose server gives no fingerprint: how many days after a fetch
  it counts as changed again (default 30).

``exclude_ids``
  Optional patterns of message ids to leave out when pairing, such as
  ``["@metadata*"]`` (MediaWiki) or ``["*.description"]`` (Signal). JSON ids
  are matched without their leading ``.``.

Templates (:file:`.pot`) are never imported.

Every language code is turned into one form, language with optional
``_REGION`` and ``@variant``, whichever way the source writes it: ``pt-BR``,
Android's ``pt-rBR`` and ``pt_br`` become ``pt_BR``; ``sr-Latn``,
``b+sr+Latn`` and ``sr@Latn`` become ``sr@latin``; ``zh-Hans`` and ``zh-Hant``
become ``zh_CN`` and ``zh_TW``; ``ca-valencia`` becomes ``ca@valencia``;
replaced codes such as ``iw`` become ``he``. Codes that aren't languages are
left out and listed by ``fetch_sources`` and ``update``.

:ref:`fetch_sources <managing#fetch-sources>` downloads the blocks into
:setting:`UPDATER_CACHE_DIR`, and :ref:`update <managing#update>` rebuilds the
database from them.


Source types
============

``damned-lies``
  GNOME's `l10n.gnome.org <https://l10n.gnome.org>`_: one tarball per language
  of a release, for every language with translations in it. ``release`` (required) is a
  release name such as ``gnome-51``; ``tarballs`` (default ``["ui"]``) can add
  ``"doc"``, or split the UI into ``"ui-libs"`` and ``"ui-apps"``.

``translationproject``
  The `Translation Project <https://translationproject.org>`_'s monthly backup
  of every package, one tarball per language.

``git``
  A shallow clone of ``url`` at ``ref`` (default: the default branch). A
  repository doesn't say which files are translations, so ``files`` is
  required and every mask must contain ``{lang}``.

``snapshot``
  Data kept in the database by :ref:`snapshot_db <managing#snapshot-db>`,
  named by ``snapshot``; nothing is downloaded and it never changes.

Both tarball sources take ``base_url`` to use a mirror.


Change detection
================

Each fetch records a fingerprint of the block: for ``damned-lies`` the
release's translation statistics (one request; a reworded translation alone
doesn't change them), for ``translationproject`` the ETag or Last-Modified
header of every tarball, for ``git`` the commit of the ref. ``fetch_sources
--check`` recomputes it without downloading and lists the blocks whose
fingerprint changed.

A server that sends neither header gives no fingerprint: such a block counts
as changed once its last fetch is ``refetch_days`` old (default 30; 0 for
every run). Every fetch also records a hash of the files it kept, and when
all the blocks :ref:`update <managing#update>` fetched are the same as last
time, it doesn't rebuild.
