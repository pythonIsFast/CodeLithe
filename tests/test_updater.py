from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from codelite import updater


def test_version_tuple_accepts_release_tags():
    assert updater._version_tuple("v1.4.2") == (1, 4, 2)


def test_version_tuple_rejects_invalid_values():
    with pytest.raises(updater.UpdateError):
        updater._version_tuple("latest")


def test_check_update_is_hidden_for_source_runs(monkeypatch):
    monkeypatch.setattr(updater, "_installed_version", lambda: (None, None))
    monkeypatch.setattr(updater, "_release", lambda: pytest.fail("must not fetch a release"))

    assert updater.check_update() == {"supported": False}


def test_check_update_finds_debian_asset(monkeypatch):
    monkeypatch.setattr(updater, "_installed_version", lambda: ("linux-deb", "1.4.1"))
    monkeypatch.setattr(
        updater,
        "_release",
        lambda: {
            "tag_name": "v1.5.0",
            "assets": [
                {"name": "code-lite_1.5.0_all.deb"},
            ],
        },
    )

    status = updater.check_update()
    assert status["available"] is True
    assert status["asset_available"] is True
    assert status["current_version"] == "1.4.1"
    assert status["latest_version"] == "1.5.0"


@pytest.mark.parametrize("platform, filename", [
    ("linux-deb", "code-lite_2.1.0_all.deb"),
    ("windows-setup", "CodeLite-2.1.0-windows-setup.exe"),
])
def test_update_uses_public_release_and_pinned_checksum(monkeypatch, tmp_path, platform, filename):
    base = updater.RELEASES_URL
    installer = b"fixture installer"
    manifest = f"{hashlib.sha256(installer).hexdigest()}  {filename}\n".encode()
    requested = []
    installed = []

    def urlopen(request, timeout):
        url = request.full_url
        requested.append(url)
        if url == f"{base}/latest":
            response = io.BytesIO()
            response.geturl = lambda: f"{base}/tag/v2.1.0"
            return response
        if url == f"{base}/download/v2.1.0/SHA256SUMS.txt":
            return io.BytesIO(manifest)
        assert url == f"{base}/download/v2.1.0/{filename}"
        return io.BytesIO(installer)

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updater, "_installed_version", lambda: (platform, "2.0.0"))
    monkeypatch.setattr(updater.tempfile, "mkdtemp", lambda **_: tmp_path)
    monkeypatch.setattr(updater, "_install_after_exit", lambda path, *_: installed.append(path))

    status = updater.check_update()
    assert status["available"] and status["asset_available"]
    assert updater.install_update() == {"status": "started", "version": "2.1.0"}
    assert installed == [tmp_path / filename]
    assert len(requested) == 5

    installer = b"tampered fixture"
    with pytest.raises(updater.UpdateError, match="SHA-256 checksum did not match"):
        updater.install_update()
    assert len(installed) == 1


def test_installer_runner_waits_for_the_current_process(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)))

    updater._install_after_exit(tmp_path / "update.deb", "linux-deb", tmp_path, 123)

    args, kwargs = calls[0]
    assert args[:2] == [updater.sys.executable, "-c"]
    assert "pkexec" in args[4]
    assert kwargs["start_new_session"] is True


def test_sha256_streams_file(tmp_path: Path):
    path = tmp_path / "installer"
    path.write_bytes(b"Code Lite")
    assert updater._sha256(path) == "820c904b18f4fee68ca8117530d9463ac083a9aed60a315aff3e8452e5347241"
