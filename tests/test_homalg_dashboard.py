"""P5 request-time overlays and private source-page boundaries."""
from __future__ import annotations

import hashlib
import http.client
import json
import threading
from pathlib import Path

import pytest
import yaml

from archon_horizon.cli import main
from archon_horizon.server.app import create_server
from archon_horizon.server.service import WorkspaceService


@pytest.fixture
def service(tmp_path: Path) -> WorkspaceService:
    workspace = tmp_path / "workspace"
    main(["--root", str(workspace), "init", "--no-interactive"])
    return WorkspaceService(workspace)


def test_overlay_freshness_does_not_mutate_graph_cache(service, monkeypatch):
    raw = {"nodes": [{"id": "Fixture2000.Thm_1", "lean_status": "lean_ok", "sources": ["Fixture2000:page-0001"]}], "edges": []}
    monkeypatch.setattr(service, "_light_dags_cached", lambda: {"HomAlg": raw})
    monkeypatch.setattr("archon_horizon.server.service.published_dag", lambda *_: raw)
    overlay = service.workspace.state_path / "overlay" / "HomAlg.json"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(json.dumps({"nodes": {"Fixture2000.Thm_1": {"headline": "in-progress", "status": "proved-pending-deps", "approx": True, "id": "malicious", "sources": ["overwrite"]}}}))
    first = service.serve_endpoint("/api/blueprints")["HomAlg"]["nodes"][0]
    assert first["headline"] == "in-progress"
    assert first["sources"] == ["Fixture2000:page-0001"]
    assert first["id"] == "Fixture2000.Thm_1"
    assert "homalg" not in raw["nodes"][0]
    overlay.write_text(json.dumps({"nodes": {"Fixture2000.Thm_1": {"headline": "proof-complete", "status": "proved"}}}))
    assert service.serve_endpoint("/api/blueprint/dag?project=HomAlg")["nodes"][0]["headline"] == "proof-complete"
    assert service.serve_endpoint("/api/overlay")["nodes"]["Fixture2000.Thm_1"]["status"] == "proved"
    overlay.unlink()
    assert "homalg" not in service.serve_endpoint("/api/blueprints")["HomAlg"]["nodes"][0]
    overlay.write_text("broken json")
    assert service.overlay() == {"nodes": {}}


def private_pages(service, tmp_path):
    private = tmp_path / "private-library"
    doc = private / "Fixture2000"
    (doc / "tex").mkdir(parents=True)
    (doc / "pages").mkdir()
    (service.root / "references").symlink_to(private, target_is_directory=True)
    text = "\\newcommand{\\C}{C}\nA synthetic transcription."
    (doc / "tex/page-0001.tex").write_text(text)
    (doc / "pages/page-0001.png").write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-test")
    (doc / "transcription.yaml").write_text(yaml.safe_dump({"pages": {"0001": {"status": "reviewed", "file_sha256": hashlib.sha256(text.encode()).hexdigest(), "reviews": [{"status": "satisfactory", "by": "fixture", "at": "2026-10-28T00:00:00Z"}]}}}))
    return doc


def test_private_root_symlink_and_reviewed_file_freshness(service, tmp_path):
    doc = private_pages(service, tmp_path)
    page = service.serve_endpoint("/api/reference-page?slug=Fixture2000&page=0001")
    assert page["status"]["status"] == "reviewed" and not page["stale"]
    assert page["text"].startswith("\\newcommand")
    (doc / "tex/page-0001.tex").write_text("changed")
    page = service.reference_page("Fixture2000", "0001")
    assert page["status"]["status"] == "transcribed" and page["stale"]
    assert yaml.safe_load((doc / "transcription.yaml").read_text())["pages"]["0001"]["status"] == "reviewed"


@pytest.mark.parametrize("slug,page", [("../secret", "0001"), ("Fixture2000/../../secret", "0001"), ("Fixture2000", "../0001"), ("Fixture2000", "0000"), ("Fixture2000", "1"), ("Fixture2000%2f..", "0001")])
def test_reference_parameter_traversal_is_rejected(service, slug, page):
    with pytest.raises(KeyError):
        service.reference_page(slug, page)


@pytest.mark.parametrize("relative", ["tex/page-0001.tex", "pages/page-0001.png", "transcription.yaml"])
def test_reference_child_symlink_escape_is_rejected(service, tmp_path, relative):
    doc = private_pages(service, tmp_path)
    secret = tmp_path / "secret"
    secret.write_text("must never be served")
    target = doc / relative
    target.unlink()
    target.symlink_to(secret)
    with pytest.raises(KeyError):
        if relative.startswith("pages/"):
            service.reference_path("Fixture2000", "0001", image=True)
        else:
            service.reference_page("Fixture2000", "0001")


def test_reference_http_image_and_private_cache_control(service, tmp_path):
    private_pages(service, tmp_path)
    server = create_server(service.root, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address)
    try:
        connection.request("GET", "/api/reference-page.png?slug=Fixture2000&page=0001")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "image/png"
        assert response.getheader("Cache-Control") == "private, no-store"
        assert response.read().startswith(b"\x89PNG")
        connection.request("GET", "/api/reference-page?slug=..%2Fsecret&page=0001")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()
