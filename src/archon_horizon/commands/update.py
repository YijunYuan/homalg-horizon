"""Install the published HomAlg fork wheel, including its compiled dashboard."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlopen

import typer

from archon_horizon.core.releases import fetch_release, release_url, release_version, wheel_asset
from archon_horizon.log import log
from .shared import emit_json

INSTALL_URL = release_url()


def update(ctx: typer.Context, as_json: bool = typer.Option(False, "--json", help="Emit JSON.")) -> None:
    """Update to the latest stable homalg-horizon release wheel."""
    try:
        release = fetch_release()
        asset = wheel_asset(release)
        with tempfile.TemporaryDirectory(prefix="homalg-horizon-update-") as directory:
            wheel = Path(directory) / asset["name"]
            with urlopen(asset["browser_download_url"], timeout=60) as response:
                data = response.read(100 * 1024 * 1024 + 1)
            if len(data) > 100 * 1024 * 1024:
                raise ValueError("release wheel exceeds the 100 MiB download limit")
            digest = asset.get("digest")
            if digest and digest != "sha256:" + hashlib.sha256(data).hexdigest():
                raise ValueError("release wheel SHA-256 digest mismatch")
            wheel.write_bytes(data)
            argv = ["uv", "tool", "install", "--force", str(wheel)] if shutil.which("uv") else [sys.executable, "-m", "pip", "install", "--upgrade", str(wheel)]
            result = subprocess.run(argv, check=False)
        if result.returncode:
            raise RuntimeError(f"wheel installer exited {result.returncode}")
    except Exception as exc:
        if as_json:
            emit_json({"updated": False, "error": str(exc), "source": release_url()})
        else:
            log.error(f"HomAlg Horizon update failed: {exc}")
        raise typer.Exit(1) from exc
    if as_json:
        emit_json({"updated": True, "version": release_version(release["tag_name"]), "source": asset["browser_download_url"]})
    else:
        log.success(f"HomAlg Horizon {release_version(release['tag_name'])} installed.")
        log.info("Run `horizon init --update` in existing workspaces to refresh bundled skills.")
