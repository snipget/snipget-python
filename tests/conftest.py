import pytest


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Fail loudly if any test would actually sleep — retry tests must
    monkeypatch the sleep seams themselves to assert on delays."""

    def _boom(seconds):  # pragma: no cover - only fires on a test bug
        raise AssertionError(f"unexpected real sleep({seconds}); patch snipget._client._sleep")

    async def _aboom(seconds):  # pragma: no cover - only fires on a test bug
        raise AssertionError(
            f"unexpected real async sleep({seconds}); patch snipget._client._async_sleep"
        )

    monkeypatch.setattr("snipget._client._sleep", _boom)
    monkeypatch.setattr("snipget._client._async_sleep", _aboom)
