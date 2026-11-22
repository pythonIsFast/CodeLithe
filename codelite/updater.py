# Copyright 2026 Code Lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Small self-updater for installed Windows and Debian builds."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

RELEASES_URL = "https://github.com/pythonIsFast/CodeLithe/releases"
USER_AGENT = "CodeLithe-Updater"
_CHECKSUM_FILE = "SHA256SUMS.txt"
_MAX_CHECKSUM_BYTES = 64 * 1024
_RELEASE_TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
_WINDOWS_UNINSTALL_KEY = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
    "{7C4F1E62-9A3B-4D18-B5C6-1F2E8A9D3B47}_is1"
)
_VERSION_RE = re.compile(r"^\d+(?:\.\d+){1,3}$")


class UpdateError(RuntimeError):
    pass


def _installed_version() -> tuple[str | None, str | None]:
    if sys.platform.startswith("linux"):
        if Path(sys.argv[0]).resolve() != Path("/opt/code-lite/code-lite.pyz"):
            return None, None
        try:
            result = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", "code-lite"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None, None
        return "linux-deb", result.stdout.strip()

    if os.name == "nt" and getattr(sys, "frozen", False):
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_UNINSTALL_KEY) as key:
                version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                install_dir = Path(str(winreg.QueryValueEx(key, "InstallLocation")[0])).resolve()
            if Path(sys.executable).resolve().parent != install_dir:
                return None, None
            return "windows-setup", version
        except (OSError, ImportError):
            return None, None

    return None, None


def _version_tuple(value: str) -> tuple[int, ...]:
    clean = value.strip().lstrip("v").split("-", 1)[0]
    if not _VERSION_RE.fullmatch(clean):
        raise UpdateError(f"Invalid release version: {value}")
    return tuple(int(part) for part in clean.split("."))


def _checksums(content: str) -> dict[str, str]:
    hashes = {}
    for line in content.splitlines():
        digest, separator, name = line.partition("  ")
        if separator and re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            hashes[name] = digest.lower()
    return hashes


def _release() -> dict[str, Any]:
    headers = {"User-Agent": USER_AGENT}
    request = urllib.request.Request(f"{RELEASES_URL}/latest", headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final_url = response.geturl()
        prefix = f"{RELEASES_URL}/tag/"
        # GitHub may canonicalize the repository's capitalization.
        tag = final_url[len(prefix):] if final_url.lower().startswith(prefix.lower()) else ""
        if not _RELEASE_TAG_RE.fullmatch(tag):
            raise UpdateError("GitHub did not redirect to a valid CodeLithe release tag.")
        downloads = f"{RELEASES_URL}/download/{tag}"
        manifest_url = f"{downloads}/{_CHECKSUM_FILE}"
        request = urllib.request.Request(manifest_url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            content = response.read(_MAX_CHECKSUM_BYTES + 1)
        if len(content) > _MAX_CHECKSUM_BYTES:
            raise UpdateError("GitHub returned an oversized release checksum file.")
        hashes = _checksums(content.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise UpdateError(f"Could not check for updates: {error}") from error
    if not hashes:
        raise UpdateError("GitHub returned an invalid release checksum file.")
    return {
        "tag_name": tag,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"{downloads}/{urllib.parse.quote(name, safe='')}",
                "sha256": digest,
            }
            for name, digest in hashes.items()
        ],
    }


def check_update() -> dict[str, Any]:
    platform, current = _installed_version()
    if not platform or not current:
        return {"supported": False}

    release = _release()
    latest = str(release.get("tag_name") or "").lstrip("v")
    assets = release.get("assets") or []
    expected = (
        f"code-lite_{latest}_all.deb"
        if platform == "linux-deb"
        else f"CodeLite-{latest}-windows-setup.exe"
    )
    names = {str(asset.get("name")): asset for asset in assets if isinstance(asset, dict)}
    available = _version_tuple(latest) > _version_tuple(current)
    return {
        "supported": True,
        "platform": platform,
        "current_version": current,
        "latest_version": latest,
        "available": available,
        "asset_available": expected in names,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except OSError as error:
        raise UpdateError(f"Could not download the update: {error}") from error


def _install_after_exit(
    installer: Path, platform: str, directory: Path, parent_pid: int
) -> None:
    restart = ["code-lite"] if platform == "linux-deb" else [sys.executable]
    command = (
        ["pkexec", "apt-get", "install", "-y", str(installer)]
        if platform == "linux-deb"
        else [str(installer)]
    )
    runner = """
import json
import os
import shutil
import signal
import subprocess
import sys
import time

parent_pid = int(sys.argv[1])
command = json.loads(sys.argv[2])
restart = json.loads(sys.argv[3])
directory = sys.argv[4]
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        os.kill(parent_pid, 0)
    except OSError:
        break
    time.sleep(0.2)
else:
    os.kill(parent_pid, signal.SIGTERM)
    for _ in range(50):
        try:
            os.kill(parent_pid, 0)
        except OSError:
            break
        time.sleep(0.2)
try:
    if subprocess.run(command).returncode == 0:
        subprocess.Popen(restart)
finally:
    shutil.rmtree(directory, ignore_errors=True)
"""
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                runner,
                str(parent_pid),
                json.dumps(command),
                json.dumps(restart),
                str(directory),
            ],
            start_new_session=True,
        )
    except OSError as error:
        raise UpdateError(f"Could not start the installer: {error}") from error


def install_update() -> dict[str, str]:
    platform, current = _installed_version()
    if not platform or not current:
        raise UpdateError("Updates are only available for installed Windows and Debian builds.")

    release = _release()
    latest = str(release.get("tag_name") or "").lstrip("v")
    if _version_tuple(latest) <= _version_tuple(current):
        raise UpdateError("CodeLithe is already up to date.")

    expected = (
        f"code-lite_{latest}_all.deb"
        if platform == "linux-deb"
        else f"CodeLite-{latest}-windows-setup.exe"
    )
    assets = {
        str(asset.get("name")): asset
        for asset in release.get("assets") or []
        if isinstance(asset, dict)
    }
    asset = assets.get(expected, {})
    url = asset.get("browser_download_url")
    expected_hash = asset.get("sha256")
    if not url or not expected_hash:
        raise UpdateError("The release does not contain the expected installer or checksums.")

    directory = Path(tempfile.mkdtemp(prefix="codelite-update-"))
    installer = directory / expected
    _download(url, installer)
    if _sha256(installer) != expected_hash:
        raise UpdateError("The downloaded installer's SHA-256 checksum did not match.")

    _install_after_exit(installer, platform, directory, os.getpid())
    return {"status": "started", "version": latest}
