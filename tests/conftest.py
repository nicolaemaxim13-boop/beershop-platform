"""No network calls to NBU during unit tests."""
import pytest
from decimal import Decimal
from datetime import datetime, timezone
@pytest.fixture(autouse=True)
def stub_nbu_rate(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, 'fetch_nbu', lambda: (Decimal('40.000000'), datetime.now(timezone.utc).date().isoformat()))
