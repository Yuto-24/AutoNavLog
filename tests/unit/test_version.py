from importlib.metadata import version

from autonavlog.version import __version__


def test_runtime_version_matches_installed_package_metadata() -> None:
    assert __version__ == version("autonavlog")
