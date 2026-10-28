from __future__ import annotations

import pytest
from archon_horizon.core import releases, version


def release():
    tag = "v0.1.5-homalg.1"
    name = "archon_horizon-0.1.5+homalg.1-py3-none-any.whl"
    return {"tag_name": tag, "assets": [{"name": name, "browser_download_url": f"https://github.com/{releases.repository()}/releases/download/{tag}/{name}"}]}


def test_only_fork_tagged_wheel_is_accepted():
    data = release()
    assert releases.release_version(data["tag_name"]) == "0.1.5+homalg.1"
    assert releases.wheel_asset(data)["name"].endswith(".whl")
    data["assets"][0]["browser_download_url"] = "https://github.com/frenzymath/Archon-Horizon/archive/refs/heads/main.zip"
    with pytest.raises(ValueError):
        releases.wheel_asset(data)
    with pytest.raises(ValueError):
        releases.release_version("v0.1.5")


def test_fork_revision_is_compared():
    assert version._release_key("0.1.5+homalg.2") > version._release_key("0.1.5+homalg.1")
    assert version._release_key("0.1.6+homalg.1") > version._release_key("0.1.5+homalg.99")


def test_disabled_version_check_never_reads_cache_or_network(monkeypatch):
    monkeypatch.setenv("ARCHON_HORIZON_NO_VERSION_CHECK", "1")
    monkeypatch.setattr(version, "_read_latest_version_cache", lambda *a: pytest.fail("read cache"))
    monkeypatch.setattr(version, "_fetch_latest_version", lambda: pytest.fail("network"))
    assert version.latest_available_version() is None
