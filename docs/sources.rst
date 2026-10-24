.. _sources:

Translation sources
*******************

The translations amaGama imports are listed in a sources file,
:file:`amagama/updater/sources.toml` unless :setting:`UPDATER_SOURCES` names
another. Each ``[[block]]`` is a set of files fetched and imported together,
such as one GNOME release:

.. code-block:: toml

    [[block]]
    id = 9
    name = "gnome-51"
    title = "GNOME"
    version = "51"
    source = "damned-lies"
    release = "gnome-51"
    licence = "GPL-2.0-or-later"

``id``
  A unique positive integer that identifies the block for good: never reuse
  or change it. Blocks are listed in id order.

``name``
  Unique; it names the block's directory in the cache and its
  :ref:`imports <importing#record>`.

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
  A shallow clone of ``url`` at ``ref`` (default: the default branch).
  ``layout`` is ``"gnu"`` for one :file:`<lang>.po` per directory, or
  ``"nongnu"`` for a :file:`<lang>/` directory of files. ``paths`` (default:
  the repository root) lists the directories to read.

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
fingerprint changed. A server that sends neither header makes its block count
as changed every time.
