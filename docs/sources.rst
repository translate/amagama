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

A sources file can include others, named relative to it, whose blocks follow
its own; block names must be unique across them all:

.. code-block:: toml

    include = ["weblate-fedora.toml"]

The included Weblate files are written by :ref:`weblate_sources
<managing#weblate-sources>`.

``name``
  The block's fixed reference: it names the block's directory in the cache
  and its :ref:`imports <importing#record>`, so never rename or reuse one.
  Blocks are read in the order they appear in the file.

``title``
  The name shown to people, such as ``GNOME`` or ``Legacy OSS``. Titles are
  meant to be translated, so keep release numbers in ``version``.

``version``
  Optional release, such as ``"51"``; shown after the title (GNOME 51).

``versions``
  Instead of ``version``, a list of releases: the block stands for one block
  per release, with ``{version}`` replaced in every setting, so adding a
  release is one more item. The name must contain ``{version}``. An item can
  be a table giving values that differ by release:

  .. code-block:: toml

      [[block]]
      name = "gnome-{version}"
      title = "GNOME"
      source = "damned-lies"
      release = "gnome-{version}"
      versions = ["50", "51"]

      [[block]]
      name = "libreoffice-{version}"
      title = "LibreOffice"
      source = "git"
      url = "https://git.libreoffice.org/translations"
      files = ["source/{lang}/**/*.po"]
      versions = [
        { version = "26.2", ref = "libreoffice-26-2" },
        { version = "26.8", ref = "libreoffice-26-8" },
      ]

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

  For ``android``, ``{lang}`` must be a locale qualifier alone (``af``,
  ``pt-rBR``, ``b+sr+Latn``): directories that add another qualifier, such
  as :file:`values-af-watch` or :file:`values-fr-feminine`, and the
  pseudo-locales ``en-rXA``, ``ar-rXB`` and ``en-rXC`` are left out.

  ``xtb`` is Chromium's: each :file:`.xtb` translation is keyed by a
  fingerprint of the English message, which is in a :file:`.grd` (and the
  :file:`.grdp` parts it includes, which ``keep`` must keep). Each
  :file:`.grd` lists its :file:`.xtb` files and their languages, so without
  a ``template`` that list pairs them:

  .. code-block:: toml

      format = "xtb"
      files = ["**/*.xtb"]
      keep = ["**/*.grdp"]

  A ``template`` pairs by path instead, with ``files`` giving the language:

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

``segment``
  Optional ``true`` to also import each multi-sentence message as sentence
  pairs, so a single sentence of a long message can match: worthwhile for
  documentation. A message is split only when its English and translation
  have the same number of sentences and no inline tag crosses a sentence
  (an element around the whole message, such as LibreOffice's ``<ahelp>``,
  is set aside first). The whole message is imported too.

``refetch_days``
  For a source whose server gives no fingerprint: how many days after a fetch
  it counts as changed again (default 30).

``min_days``
  Days after a fetch before the block is checked again at all (default 0):
  for many small sources, so each is fetched at most that often. Blocks
  first fetched together would all come due on one day, so a block's first
  refetch comes after a part of ``min_days`` fixed by its name, which keeps
  them spread out.

``exclude_ids``
  Optional patterns of message ids to leave out, such as ``["@metadata*"]``
  (MediaWiki), ``["*.description"]`` (Signal) or a TMX ``tuid`` prefix
  (``["foundation-website-content:*"]``). JSON ids are matched without their
  leading ``.``.

Templates (:file:`.pot`) are never imported.

Every language code is turned into one form, language with optional
``_REGION`` and ``@variant``, whichever way the source writes it: ``pt-BR``,
Android's ``pt-rBR`` and ``pt_br`` become ``pt_BR``; ``sr-Latn``,
``b+sr+Latn`` and ``sr@Latn`` become ``sr@latin``; ``zh-Hans`` and ``zh-Hant``
become ``zh_CN`` and ``zh_TW``; ``ca-valencia`` becomes ``ca@valencia``;
replaced codes such as ``iw`` become ``he``; and a three-letter ISO 639 code
with a two-letter one becomes it (``arg`` and ``deu`` become ``an`` and
``de``). Codes that aren't languages are
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

``drupal``
  `localize.drupal.org <https://localize.drupal.org>`_: a :file:`.po` per
  language of the latest stable release of each of ``projects`` (required),
  such as ``["drupal", "token"]``. With ``core``, a Drupal major version
  such as ``"11"``, each release is the newest for that core: core's own
  in that series, and a module's whose core compatibility includes it.
  The languages are the translation
  server's; a language with no file for a project is skipped. ``base_url``
  and ``releases_url`` can name mirrors of the files and the release
  history; ``pause`` (default 1) is the seconds between downloads.

``translationproject``
  The `Translation Project <https://translationproject.org>`_'s monthly backup
  of every package, one tarball per language.

``git``
  A shallow clone of ``url`` at ``ref`` (default: the default branch). A
  repository doesn't say which files are translations, so ``files`` is
  required and every mask must contain ``{lang}``. ``sparse``, a list of
  gitignore-style patterns, downloads and checks out only the matching
  files, for a large repository:

  .. code-block:: toml

      sparse = ["**/res/values*/strings.xml"]

``http``
  One file per language under a directory listing, such as Debian's
  compendia: ``base_url`` is the listing, whose subdirectories are the
  languages, and ``path`` the file under it, with ``{lang}``. Where that file
  is missing (403 or 404), ``fallback``, a filemask with ``{lang}`` and
  ``*``, picks the last matching file in the listing of its directory; a
  language with neither is skipped. ``pause`` (default 1) is the seconds
  between downloads.

``svn``
  A Subversion tree with one directory per language, such as KDE's
  ``l10n-kf6``. ``url`` (required) is the tree; ``dirs`` lists what to export
  from each language's directory (default: all of it), such as
  ``["messages"]``; ``pause`` (default 1) is the seconds between exports, to
  go easy on a shared server. Needs the ``svn`` command.

``joomla``
  `Joomla <https://www.joomla.org>`_'s accredited language packs for a major
  ``release`` (required), such as ``"5"``, with the English they translate
  from: the files of joomla-cms at ``ref`` (required), such as
  ``"5.4-dev"``, that ``template`` (required) matches. Each pack's files are
  placed where Joomla installs them, beside the English:

  .. code-block:: toml

      files = ["**/language/{lang}/*.ini"]
      format = "joomla-ini"
      template = "**/language/en-GB/*.ini"

  A pack whose SHA-256 doesn't match its details is skipped. ``base_url``
  and ``repository`` can name mirrors; ``pause`` (default 1) is the seconds
  between downloads.

``moodle``
  `Moodle <https://moodle.org>`_'s language packs for ``release`` (required),
  such as ``"5.3"``: a zip per language, English included, from the index
  Moodle's own language installer reads. Each pack holds
  :file:`{lang}/{component}.php`, so the English pack is the template:

  .. code-block:: toml

      files = ["{lang}/*.php"]
      format = "php"
      template = "en/*.php"

  Downloads are checked against the index's MD5s. ``base_url`` can name a
  mirror; ``pause`` (default 1) is the seconds between downloads.

``pontoon``
  Mozilla's `Pontoon <https://pontoon.mozilla.org>`_: each locale's whole
  translation memory as one TMX file, holding only approved translations,
  for every locale with at least ``min_approved`` (default 1) approved
  strings. ``base_url`` can name another Pontoon; ``pause`` (default 1) is
  the seconds between downloads.

``vscode``
  `VS Code <https://code.visualstudio.com>`_'s language packs from
  `vscode-loc <https://github.com/microsoft/vscode-loc>`_ (``repository``,
  ``ref`` default ``main``), paired with the English of the VS Code release
  the packs target, from Microsoft's CDN (``update_url``, ``cdn_url``), as
  PO files. Strings whose English the web build doesn't have (node-only
  modules) are left out.

``wordpress``
  `WordPress.org <https://translate.wordpress.org>`_'s language packs: one
  zip per language, each holding :file:`.po` files (select them with
  ``files = ["**/*.po"]``). ``kind`` (required) is ``core``, ``plugins`` or
  ``themes``; ``slugs`` lists the plugins or themes. For ``core``,
  ``release`` (such as ``"7.1"``) takes the pack each language has for that
  release, which may be an older one; without it, each language's newest
  pack. WordPress's formal, informal and ``ao90`` variants are kept with
  their language. ``base_url`` can name a mirror of the API; ``pause``
  (default 1) is the seconds between downloads.

``snapshot``
  Data kept in the database by :ref:`snapshot_db <managing#snapshot-db>`,
  named by ``snapshot``; nothing is downloaded and it never changes.

Both tarball sources take ``base_url`` to use a mirror, and ``pause``
(default 1), the seconds between downloads.


Change detection
================

Each fetch records a fingerprint of the block: for ``damned-lies`` the
release's translation statistics (one request; a reworded translation alone
doesn't change them), for ``wordpress`` every pack's version and update time,
for ``moodle`` the pack index with its MD5s, for ``joomla`` the packs'
versions and the English ref's commit, for ``drupal`` the projects' releases
(the files are rebuilt daily), for ``pontoon`` none (its files are generated
on request), for ``translationproject`` the ETag or Last-Modified header of
every tarball, for ``git`` and ``vscode`` the commit of the ref (from Gitiles'
JSON on ``googlesource.com``, where ``ls-remote`` is too costly).
``fetch_sources --check`` recomputes it without downloading and lists the
blocks whose fingerprint changed.

A server that sends neither header gives no fingerprint: such a block counts
as changed once its last fetch is ``refetch_days`` old (default 30; 0 for
every run). Every fetch also records a hash of the files it kept, and when
all the blocks :ref:`update <managing#update>` fetched are the same as last
time, it doesn't rebuild.
