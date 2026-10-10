import pytest

from src.pipeline._db import forced_download, forced_import


@pytest.mark.parametrize(
    ("env", "download", "import_"),
    [
        ({}, False, False),
        ({"ATP2OSM_FORCE": "1"}, True, True),
        ({"ATP2OSM_FORCE_DOWNLOAD": "1"}, True, False),
        ({"ATP2OSM_FORCE_IMPORT": "1"}, False, True),
    ],
)
def test_force_sets_both_switches_and_each_sets_its_own(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], download: bool, import_: bool
) -> None:
    for name in ("ATP2OSM_FORCE", "ATP2OSM_FORCE_DOWNLOAD", "ATP2OSM_FORCE_IMPORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert (forced_download(), forced_import()) == (download, import_)
