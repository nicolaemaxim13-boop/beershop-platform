"""Local-only browser fixture. Never use this app in deployment."""
import os
from datetime import date
from decimal import Decimal

if not os.getenv('DATABASE_URL', '').startswith('sqlite:///') or os.getenv('BEERSHOP_PERSISTENT_DATABASE_URL'):
    raise RuntimeError('CRM browser fixtures require an isolated SQLite database')

import app.main as main
main.fetch_nbu = lambda: (Decimal('40.00'), date.today().isoformat())
app = main.app
