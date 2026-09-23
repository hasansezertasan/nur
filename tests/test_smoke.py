import nur


def test_version_is_non_empty_string() -> None:
    assert isinstance(nur.__version__, str)
    assert nur.__version__
