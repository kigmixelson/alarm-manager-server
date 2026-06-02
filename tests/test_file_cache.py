import time

from alarm_manager_server.cache import FileCache


def test_file_cache_roundtrip(tmp_path):
    cache = FileCache(tmp_path, enabled=True)
    cache.set("demo", {"a": 1})
    assert cache.get("demo", ttl_sec=60) == {"a": 1}


def test_file_cache_expires(tmp_path):
    cache = FileCache(tmp_path, enabled=True)
    cache.set("demo", [1, 2])
    path = cache.path_for("demo")
    envelope = __import__("json").loads(path.read_text(encoding="utf-8"))
    envelope["saved_at"] = time.time() - 100
    path.write_text(__import__("json").dumps(envelope), encoding="utf-8")
    assert cache.get("demo", ttl_sec=30) is None


def test_file_cache_disabled(tmp_path):
    cache = FileCache(tmp_path, enabled=False)
    cache.set("demo", "x")
    assert cache.get("demo", ttl_sec=60) is None
    assert not cache.path_for("demo").exists()


def test_ttl_zero_skips_read(tmp_path):
    cache = FileCache(tmp_path, enabled=True)
    cache.set("demo", "ok")
    assert cache.get("demo", ttl_sec=0) is None
