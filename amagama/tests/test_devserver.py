import logging
import sys

from amagama import devserver


class FakeServer(object):
    """Stands in for cheroot's server; start() returns at once."""
    instances = []

    def __init__(self, bind_addr, application):
        self.bind_addr = bind_addr
        self.application = application
        self.stopped = False
        FakeServer.instances.append(self)

    def start(self):
        raise KeyboardInterrupt

    def stop(self):
        self.stopped = True


def test_main_options(monkeypatch):
    app = object()
    launched = []
    levels = []
    monkeypatch.setattr(devserver, 'amagama_server_factory', lambda: app)
    monkeypatch.setattr(devserver, 'launch_server',
                        lambda *args: launched.append(args))
    monkeypatch.setattr(logging, 'basicConfig',
                        lambda **kwargs: levels.append(kwargs['level']))

    monkeypatch.setattr(sys, 'argv', ['amagama'])
    devserver.main()
    monkeypatch.setattr(sys, 'argv',
                        ['amagama', '-b', '0.0.0.0', '-p', '9999', '--debug'])
    devserver.main()

    assert launched == [('localhost', 8888, app), ('0.0.0.0', 9999, app)]
    assert levels == [logging.WARNING, logging.DEBUG]


def test_launch_server_cheroot(monkeypatch):
    import cheroot.wsgi
    monkeypatch.setattr(cheroot.wsgi, 'Server', FakeServer)
    app = object()
    devserver.launch_server('localhost', 8888, app)
    server = FakeServer.instances[-1]
    assert server.bind_addr == ('localhost', 8888)
    assert server.application is app
    # Ctrl+C stops the server.
    assert server.stopped


def test_launch_server_without_cheroot(monkeypatch):
    import werkzeug.serving
    monkeypatch.setitem(sys.modules, 'cheroot.wsgi', None)
    calls = []
    monkeypatch.setattr(werkzeug.serving, 'run_simple',
                        lambda *args: calls.append(args))
    app = object()
    devserver.launch_server('localhost', 8888, app)
    assert calls == [('localhost', 8888, app)]
