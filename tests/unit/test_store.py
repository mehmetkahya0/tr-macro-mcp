import datetime as dt
import subprocess
import sys
from pathlib import Path

from tr_macro_mcp.store.timeseries import Store, utcnow


def test_two_processes_can_share_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "cache.duckdb"
    store = Store(path)
    store.put_response("k", b"from-test", utcnow())
    code = (
        "import datetime as dt, sys\n"
        "from pathlib import Path\n"
        "from tr_macro_mcp.store.timeseries import Store, utcnow\n"
        f"s = Store(Path({str(path)!r}))\n"
        "assert s.get_response('k', dt.timedelta(hours=1)).body == b'from-test'\n"
        "s.put_response('k2', b'from-child', utcnow())\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    cached = store.get_response("k2", dt.timedelta(hours=1))
    assert cached is not None
    assert cached.body == b"from-child"


def test_expired_responses_are_ignored(tmp_path: Path) -> None:
    store = Store(tmp_path / "cache.duckdb")
    store.put_response("k", b"old", utcnow() - dt.timedelta(days=2))
    assert store.get_response("k", dt.timedelta(days=1)) is None
