#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest
from translate.storage.po import pofile

from amagama.tests.test_pairing import block, git_repo, lookup, update  # noqa: F401
from amagama.updater.segment import segmented, sentence_pairs


@pytest.mark.parametrize("source, target, tlang, expected", [
    ("Choose Format. Then click OK.", "Choisissez Format. Cliquez ensuite sur OK.", "fr",
     [("Choose Format.", "Choisissez Format."), ("Then click OK.", "Cliquez ensuite sur OK.")]),
    # Balanced inline markup stays with its sentence.
    ("Choose <emph>Format - Cells</emph>. Then click OK.",
     "Choisissez <emph>Format - Cellules</emph>. Cliquez ensuite sur OK.", "fr",
     [("Choose <emph>Format - Cells</emph>.", "Choisissez <emph>Format - Cellules</emph>."),
      ("Then click OK.", "Cliquez ensuite sur OK.")]),
    # LibreOffice wraps whole paragraphs in <ahelp>.
    ('<ahelp hid=".">Opens the dialog. Use it to set values.</ahelp>',
     '<ahelp hid=".">Ouvre la boîte de dialogue. Utilisez-la pour définir des valeurs.</ahelp>',
     "fr", [("Opens the dialog.", "Ouvre la boîte de dialogue."),
            ("Use it to set values.", "Utilisez-la pour définir des valeurs.")]),
    ("Choose Format. Then click OK.", "选择格式。然后单击确定。", "zh_CN",
     [("Choose Format.", "选择格式。"), ("Then click OK.", "然后单击确定。")]),
    # Self-closing tags don't need closing (GNOME's Mallard links).
    ('See <link xref="intro"/>. Then click OK.', 'Voir <link xref="intro"/>. Cliquez ensuite sur OK.',
     "fr", [('See <link xref="intro"/>.', 'Voir <link xref="intro"/>.'),
            ("Then click OK.", "Cliquez ensuite sur OK.")]),
    # Kept whole: one sentence, different counts, a tag across sentences.
    ("Click OK.", "Cliquez sur OK.", "fr", []),
    ("Choose Format. Then click OK.", "Choisissez Format puis cliquez sur OK.", "fr", []),
    ("Open <b>the dialog. Use</b> it.", "Ouvrez <b>la boîte. Utilisez</b>-la.", "fr", []),
    ("Close </b>it. Then <b>open it.", "Fermez </b>la. Puis <b>ouvrez-la.", "fr", []),
])
def test_sentence_pairs(source, target, tlang, expected):
    assert sentence_pairs(source, target, "en", tlang) == expected


def test_segmented_keeps_only_usable_units():
    store = pofile()
    store.parse(b'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                b'msgid "Choose Format. Then click OK."\n'
                b'msgstr "Choisissez Format. Cliquez ensuite sur OK."\n\n'
                b'#, fuzzy\nmsgid "Save it. Close it."\nmsgstr "Enregistrez. Fermez."\n')
    result = segmented(store, "en", "fr", lambda unit: unit.istranslated())
    assert [(u.source, u.target) for u in result.units] == [
        ("Choose Format. Then click OK.", "Choisissez Format. Cliquez ensuite sur OK."),
        ("Choose Format.", "Choisissez Format."),
        ("Then click OK.", "Cliquez ensuite sur OK."),
    ]


HELP = ('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Choose Format. Then click OK."\n'
        'msgstr "Choisissez Format. Cliquez ensuite sur OK."\n')


@pytest.mark.parametrize("segment, found", [(True, ["Cliquez ensuite sur OK."]), (False, [])])
def test_update(update, tmp_path, segment, found):  # noqa: F811 - fixture
    url = git_repo(tmp_path / "repo", {"help/fr.po": HELP})
    result = update(block("help", "git", url=url, files=["help/{lang}.po"], segment=segment))
    assert result.exit_code == 0, result.output
    assert ("Imported 1 files: %d units (0 skipped)." % (3 if segment else 1)) in result.output
    assert lookup("fr", "Then click OK.") == found
