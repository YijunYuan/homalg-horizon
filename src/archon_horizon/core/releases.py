"""Release metadata for the HomAlg fork; never install upstream branch sources."""
from __future__ import annotations

import json
import os
import re
from urllib.request import Request, urlopen

DEFAULT_REPOSITORY = "YijunYuan/homalg-horizon"


def repository() -> str:
    value = os.environ.get("HOMALG_HORIZON_REPOSITORY", DEFAULT_REPOSITORY)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("HOMALG_HORIZON_REPOSITORY must be owner/repository")
    return value


def release_url() -> str:
    return f"https://api.github.com/repos/{repository()}/releases/latest"


def release_version(tag: object) -> str:
    match = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)-homalg\.([0-9]+)", str(tag))
    if match is None:
        raise ValueError("release tag must be vMAJOR.MINOR.PATCH-homalg.N")
    return f"{match[1]}+homalg.{match[2]}"


def fetch_release(timeout: float = 15) -> dict:
    request = Request(release_url(), headers={"User-Agent": "homalg-horizon", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise ValueError("expected a published stable HomAlg release")
    release_version(data.get("tag_name"))
    return data


def wheel_asset(release: dict) -> dict:
    version = release_version(release.get("tag_name"))
    filename = f"archon_horizon-{version}-py3-none-any.whl"
    assets = [a for a in release.get("assets", []) if isinstance(a, dict) and a.get("name") == filename]
    if len(assets) != 1:
        raise ValueError(f"release must contain exactly one {filename}")
    asset = assets[0]
    expected = f"https://github.com/{repository()}/releases/download/{release['tag_name']}/{filename}"
    # GitHub may URL-encode the PEP 440 '+' local-version separator.
    if str(asset.get("browser_download_url", "")).replace("%2B", "+").replace("%2b", "+") != expected:
        raise ValueError("wheel download must belong to this fork's tagged release")
    return asset
