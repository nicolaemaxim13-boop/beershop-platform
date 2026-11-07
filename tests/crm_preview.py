"""Local-only browser fixture. Never use this app in deployment."""
import os
from datetime import date
from decimal import Decimal

if not os.getenv('DATABASE_URL', '').startswith('sqlite:///') or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL'):
    raise RuntimeError('CRM browser fixtures require an isolated SQLite database')

import app.main as main
import app.connectors41 as connectors
main.fetch_nbu = lambda: (Decimal('40.00'), date.today().isoformat())

def no_provider_network(*args, **kwargs):
    raise RuntimeError('Provider access is forbidden in the browser fixture')

connectors.build_opener = no_provider_network
app = main.app
