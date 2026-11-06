"""No network calls to NBU during unit tests."""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
@pytest.fixture(autouse=True)
def stub_nbu_rate(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'fetch_nbu', lambda: (Decimal('40.000000'), datetime.now(timezone.utc).date().isoformat()))

@pytest.fixture(autouse=True)
def block_provider_network(monkeypatch):
    import app.connectors41 as connector
    def blocked(*args, **kwargs):
        raise AssertionError('Provider network is forbidden in tests; install an explicit mock')
    monkeypatch.setattr(connector, 'build_opener', blocked)

