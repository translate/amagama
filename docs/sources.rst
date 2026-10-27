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
<managing#weblate-sources>`; :ref:`transifex_sources
<managing#transifex-sources>` writes blocks the same way from repositories'
Transifex configurations, and :ref:`discover_sources
<managing#discover-sources>` from repositories it finds by the localisation
configuration they commit.

A ``[defaults]`` table gives settings for every block in its file (not in the
files it includes); a block's own settings take precedence. ``name`` and
``versions`` can't be defaults:

.. code-block:: toml

    [defaults]
    source = "git"
    min_age_days = 60

``name``
  The block's fixed reference: it names the block's directory in the cache
  and its :ref:`imports <importing#record>`, so never rename or reuse one.
  Blocks are read in the order they appear in the file.

``title``
  The name shown to people, such as ``GNOME`` or ``Legacy OSS``. Titles are
  :ref:`translated <sources#titles>`, so keep release numbers in ``version``.

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
  ``template``: ``android`` (:file:`strings.xml`), ``php``, ``properties``
  (Java's ISO-8859-1; ``properties-utf8`` and ``properties-utf16`` for files in
  those encodings), ``joomla-ini``, ``json`` (key to message), ``fluent`` and
  ``dtd``.

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
  whether the first row is a header to skip, or the header row's first cell,
  so that the rows before it (a notice, say) are skipped too. Files are then
  read as plain comma-separated values:

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

``weight``
  Optional number from 0 to 10, default 1: how suggestions from the block
  rank in lookups, by quality times weight, unless a client gives its own
  :ref:`weight <api#providing-options>`. A translation also found in a block
  of higher weight takes that weight. Set it in a file's ``[defaults]`` to
  weight all of an included file's blocks.

``tags``
  Optional list grouping blocks apart from the file they're in, so a server
  can :ref:`build a set of them <managing#update>`. A file's ``[defaults]``
  tags add to each block's own. A block has at most one tier, how it was
  chosen:

  - ``core``: by hand (:file:`sources.toml`)
  - ``platform``: from a translation platform's listing (``weblate_sources``)
  - ``discovered``: by a search of repositories (``discover_sources``,
    ``transifex_sources``)

  and a content tag, ``ui``, ``docs`` or ``terminology``; without one it is
  ``docs`` if ``segment`` is set, else ``ui``. ``trial`` marks blocks being
  tried out:

  .. code-block:: toml

      tags = ["terminology", "trial"]

``disabled``
  Optional reason to leave the block out: it isn't fetched, imported or
  listed, but keeps its name. The commands that write blocks keep it when
  they regenerate the file:

  .. code-block:: toml

      disabled = "duplicates Damned Lies module meld"

``min_age_days`` and ``max_age_days``
  Both count days since the block was last fetched; ``min_age_days`` is a
  floor and ``max_age_days`` a ceiling.

  ``min_age_days`` (default 0): until the block's copy is this old, it isn't
  checked at all, so its server gets no request; this keeps many small
  sources from being fetched more often than they're worth. Blocks first
  fetched together would all come due on one day, so a block's first
  refetch comes after a fraction of ``min_age_days`` taken from a hash of its
  name. 0 checks the block on every run.

  ``max_age_days`` (default 30): a copy this old is fetched again, though
  nothing says it changed. For a source with one download per language
  (``damned-lies``, ``translationproject``, ``http``, ``pontoon``,
  ``wordpress``, ``drupal``, ``moodle``, ``joomla``, ``svn``) it applies to
  each language, and the downloads are spread out: a run takes its share of
  the languages for the days since the last fetch (rounded up; a day's share
  the first time), languages never fetched first, then those fetched longest
  ago. With 57 languages a daily run fetches 2, so the first fetch takes
  ``max_age_days`` too, and the block isn't due again until
  ``max_age_days`` / 57 days later. Such a source is refreshed by age, not by
  its fingerprint, so its server never gets every language at once. 0 turns that off: the whole block is fetched whenever its
  fingerprint changes, or on every run without one.

  An old release that hardly changes can have a longer ``max_age_days`` than
  the current ones, as an item of ``versions``.

  Within ``min_age_days`` a block isn't touched, even past ``max_age_days``.

``exclude_ids``
  Optional patterns of message ids to leave out, such as ``["@metadata*"]``
  (MediaWiki), ``["*.description"]`` (Signal) or a TMX ``tuid`` prefix
  (``["foundation-website-content:*"]``). JSON ids are matched without their
  leading ``.``.

``language_in_ids``
  Optional ``true`` for a block with a ``template`` whose message ids name
  their file's language, such as CLDR's ``main.de.localeDisplayNames....``
  or Rails' ``de.errors....``: the id segment where every one of the
  template's ids has the same English code is left out of each id before a
  translation is matched with its English.

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
  required and every mask must contain ``{lang}``. Only the files the block
  reads are downloaded and checked out: ``files`` with ``{lang}`` as ``*``,
  ``template`` and ``keep``, or ``sparse``, a list of gitignore-style
  patterns, instead:

  .. code-block:: toml

      sparse = ["**/res/values*/strings.xml"]

  The clone is kept, one per ``url``, in ``.repos`` in
  :setting:`UPDATER_CACHE_DIR`, and shared by the blocks that fetch from it:
  a release's or a component's block downloads only what changed since any
  of them last fetched.

  ``mirror`` is a copy of ``url`` to fetch from instead, to take load off a
  community project's servers, such as GNOME's GitHub copy of a
  gitlab.gnome.org repository. ``url`` stays the block's repository. Each
  check compares the two refs' commits: the mirror is used while it has
  ``url``'s commit, or is behind but moved less than ``mirror_lag_days``
  (default 14, a block setting that ``[defaults]`` can give) ago. After
  that, or when it can't be reached, ``url`` is, and ``fetch_sources`` and
  ``update`` say so; a mirror that hasn't moved for twice that is asked
  about:

  .. code-block:: toml

      url = "https://gitlab.gnome.org/GNOME/meld"
      mirror = "https://github.com/GNOME/meld"

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

``glotpress``
  A `GlotPress <https://glotpress.blog>`_ project's translations, for
  WordPress.org's projects without language packs: one :file:`.po` export
  of the current translations per locale and translation set, of the locales
  with anything translated.
  ``project`` (required) is the project's path, such as ``apps/android/dev``.
  A formal or other set goes with its language. ``base_url`` (default
  ``https://translate.wordpress.org``) names the GlotPress site; ``pause``
  (default 5) is the seconds between exports, and a 429 Too Many Requests is
  retried after ``retry_wait`` (default 60) seconds, then twice that.

``snapshot``
  Data kept in the database by :ref:`snapshot_db <managing#snapshot-db>`,
  named by ``snapshot``; nothing is downloaded and it never changes.

Both tarball sources take ``base_url`` to use a mirror, and ``pause``
(default 1), the seconds between downloads.


Change detection
================

Each fetch records a fingerprint of the block: for ``damned-lies`` the
release's translation statistics (one request; a reworded translation alone
doesn't change them), for ``wordpress`` every pack's version and update time, for ``glotpress`` the
project page's translated count per locale (one request; like Damned Lies,
a rewording alone doesn't change it),
for ``moodle`` the pack index with its MD5s, for ``joomla`` the packs'
versions and the English ref's commit, for ``drupal`` the projects' releases
(the files are rebuilt daily), for ``pontoon`` none (its files are generated
on request), for ``translationproject`` the ETag or Last-Modified header of
every tarball, for ``git`` and ``vscode`` the commit of the ref (from Gitiles'
JSON on ``googlesource.com``, where ``ls-remote`` is too costly).
``fetch_sources --check`` recomputes it without downloading and lists the
blocks whose fingerprint changed.

A server that sends neither header gives no fingerprint: such a block counts
as changed once its last fetch is ``max_age_days`` old. A block with one
download per language refreshes its languages in turn instead, and
``fetch_sources --check`` lists it when some are due. A block younger than
``min_age_days`` isn't checked at all. Every fetch also records a hash of the files it kept, and when
all the blocks :ref:`update <managing#update>` fetched are the same as last
time, it doesn't rebuild.


.. _sources#titles:

Translated titles
=================

Block titles are translated in :file:`amagama/po/sources/`: one
:file:`<lang>.po` per language, from the template :file:`sources.pot`. Beside
the titles, translators word "{title} {version}" for their language and give
its text direction. After adding or changing a title, here or with
:ref:`weblate_sources <managing#weblate-sources>`, :ref:`transifex_sources
<managing#transifex-sources>` or :ref:`discover_sources
<managing#discover-sources>`, regenerate the template;
the tests fail while it's out of date:

.. code-block:: bash

    $ amagama-manage sources_pot
