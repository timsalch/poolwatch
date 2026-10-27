import json
import threading
import urllib.error
import urllib.request

import pytest

from poolwatch.history import EventStore
from poolwatch.web import safe_image_path, serve

from conftest import at


@pytest.fixture
def site(tmp_path):
    data = tmp_path / "data"
    store = EventStore(data)
    (data / "frames").mkdir()
    img = data / "frames" / "a.jpg"
    img.write_bytes(b"\xff\xd8jpeg")
    (tmp_path / "secret.jpg").write_bytes(b"nope")
    store.append({"ts": at(12), "image": str(img), "frames": [], "summary": {"headline": "hi"}})
    store.append({"ts": at(12, 3), "image": str(img), "frames": [], "summary": {"headline": "two"}})
    store.write_state({"now": at(12, 3), "latest_event_id": 2, "latest_image_event_id": 2})
    server = serve(store, [data], port=0, background=True)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, img, tmp_path
    server.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_home_page_and_state(site):
    base, _, _ = site
    status, ctype, body = get(base + "/")
    assert status == 200 and ctype.startswith("text/html") and b"poolwatch" in body
    _, _, body = get(base + "/api/state")
    data = json.loads(body)
    assert data["state"]["latest_event_id"] == 2 and data["latest"]["summary"]["headline"] == "two"


def test_event_detail_has_neighbors(site):
    base, _, _ = site
    data = json.loads(get(base + "/api/events/2")[2])
    assert data["event"]["id"] == 2 and data["prev"] == 1 and data["next"] is None
    assert [e["id"] for e in json.loads(get(base + "/api/events?limit=5")[2])] == [2, 1]
    with pytest.raises(urllib.error.HTTPError) as err:
        get(base + "/api/events/99")
    assert err.value.code == 404


def test_images_are_served_only_from_allowed_folders(site):
    base, img, tmp_path = site
    from urllib.parse import quote
    status, ctype, body = get(base + "/image?p=" + quote(str(img)))
    assert status == 200 and ctype == "image/jpeg" and body.startswith(b"\xff\xd8")
    for bad in [str(tmp_path / "secret.jpg"), str(img.parent / ".." / ".." / "secret.jpg")]:
        with pytest.raises(urllib.error.HTTPError) as err:
            get(base + "/image?p=" + quote(bad))
        assert err.value.code == 404


def test_safe_image_path_rejects_non_images(tmp_path):
    (tmp_path / "config.toml").write_text("x")
    assert safe_image_path(str(tmp_path / "config.toml"), [tmp_path]) is None
    assert safe_image_path("", [tmp_path]) is None
