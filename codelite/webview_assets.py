"""Expose bundled pywebview scripts as files for its native bridge."""

from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from zipimport import zipimporter


def _copy_resources(source, target: Path) -> None:
    target.mkdir()
    for entry in source.iterdir():
        destination = target / entry.name
        if entry.is_dir():
            _copy_resources(entry, destination)
        else:
            destination.write_bytes(entry.read_bytes())


@contextmanager
def webview_script_files(webview):
    if not isinstance(webview.__loader__, zipimporter):
        yield
        return

    from webview import util

    # pywebview uses os.walk and open on these resources. Keep the extracted
    # scripts alive until its native loop has finished loading pages.
    with TemporaryDirectory(prefix="codelite-webview-") as folder:
        scripts = Path(folder) / "js"
        _copy_resources(files(webview).joinpath("js"), scripts)
        get_js_dir = util.get_js_dir
        util.get_js_dir = lambda: str(scripts)
        try:
            yield
        finally:
            util.get_js_dir = get_js_dir
