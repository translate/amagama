import os
import random
import signal

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tmdb import BUILD_PREFIX
from amagama.updater.pipeline import import_files, in_workers

LANGS = ["af", "zu", "xh", "st", "tn", "nso", "ts", "ve", "nr", "ss", "de", "fr", "nl", "es", "it", "pt"]
POOL = ["String %d" % n for n in range(3000)]


def test_parallel_overlapping_imports(amagama_config, tmp_path, pg_connection):
    """Workers importing the same new sources at once all succeed and lose nothing."""
    # Each file holds a different subset of the pool, so workers keep
    # inserting sources that another worker added but hasn't committed yet.
    rng = random.Random(1)
    tasks = []
    expected_targets = 0
    sources = set()
    for lang in LANGS:
        for part in range(4):
            path = tmp_path / ("%s-%d.po" % (lang, part))
            subset = rng.sample(POOL, 600)
            sources.update(subset)
            expected_targets += len(subset)
            units = "".join('\nmsgid "%s"\nmsgstr "%s %s %d"\n' % (s, s, lang, part) for s in subset)
            path.write_text('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n'
                            + units, encoding="utf-8")
            tasks.append((str(path), "en", lang, "stress", None))
    assert CliRunner().invoke(manage, ("initdb", "-s", "en", "--build")).exit_code == 0
    from amagama.application import amagama_server_factory
    app = amagama_server_factory()
    with app.app_context():
        app.tmdb.use_schemas(BUILD_PREFIX)
        results = import_files(tasks, 4)
    assert results == [(600, 0)] * len(tasks)
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM _build_en.sources")
        assert cursor.fetchone()[0] == len(sources)
        cursor.execute("SELECT COUNT(*) FROM _build_en.targets")
        assert cursor.fetchone()[0] == expected_targets


def _killed_on_two(n):
    if n == 2:
        os.kill(os.getpid(), signal.SIGKILL)
    return n


def test_in_workers_keeps_order():
    assert in_workers(abs, [-1, -2, 3, -4], 2) == [1, 2, 3, 4]


def test_dead_worker_raises():
    with pytest.raises(RuntimeError, match="worker died"):
        in_workers(_killed_on_two, [1, 2, 3], 2)
