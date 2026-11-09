#!/usr/bin/env python
"""
Cross-service integration verification runner.

Validates the full end-to-end flow across:
  user_service -> operation_service -> report_service
with verification of:
  - User registration and JWT authentication
  - Product creation and stock tracking
  - Order creation and payment
  - Reporting snapshot synchronization
  - Cascade & isolation: stale-record removal when test entities are purged

All tests execute against dedicated PostgreSQL test databases (PostgreSQL only; no SQLite).
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

REPO_ROOT = Path(__file__).resolve().parent.parent


def get_service_test_db_url(service_dir: str, default_db: str) -> str:
    env_var = f"{service_dir.upper()}_TEST_DATABASE_URL"
    if os.getenv(env_var):
        return os.environ[env_var]
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]

    # Read the service settings
    sys_path_orig = list(sys.path)
    try:
        sys.path.insert(0, str(REPO_ROOT / service_dir))
        # Clear cached app modules
        for mod in list(sys.modules.keys()):
            if mod == "app" or mod.startswith("app."):
                del sys.modules[mod]
        # Change working directory so pydantic loads service .env if present
        cwd = os.getcwd()
        try:
            os.chdir(str(REPO_ROOT / service_dir))
            from app.core.config import settings

            base_url = settings.database_url
            parsed = urlparse(base_url)
            return urlunparse(parsed._replace(path=f"/{default_db}"))
        finally:
            os.chdir(cwd)
    finally:
        sys.path = sys_path_orig


def run_integration_check() -> int:
    print("=" * 70)
    print("STARTING CROSS-SERVICE INTEGRATION CHECK (POSTGRESQL ONLY)")
    print("=" * 70)

    u_db_url = get_service_test_db_url("user_service", "user_service_test_db")
    o_db_url = get_service_test_db_url("operation_service", "operation_service_test_db")
    r_db_url = get_service_test_db_url("report_service", "report_service_test_db")

    script = f"""
import sys, os
from decimal import Decimal
from pathlib import Path
from fastapi.testclient import TestClient
import respx
from httpx import Response

root = Path(r'{REPO_ROOT}')
sys.path.insert(0, str(root / 'libs' / 'testing'))
from coffee_testing import marked_email, auth_header

os.environ['ENVIRONMENT'] = 'test'
os.environ['JWT_SECRET_KEY'] = 'dev-only-shared-secret-change-me'

# --- 1. USER SERVICE ---
for k in list(sys.modules.keys()):
    if k == 'app' or k.startswith('app.'):
        del sys.modules[k]
sys.path = [str(root / 'user_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
os.environ['DATABASE_URL'] = r'{u_db_url}'

from app.main import app as u_app
from app.core.database import Base as UBase, SessionLocal as USession, engine as u_engine
UBase.metadata.drop_all(bind=u_engine)
UBase.metadata.create_all(bind=u_engine)
u_client = TestClient(u_app)

cust_email = marked_email('integ_customer')
r1 = u_client.post('/user', json={{'username': 'test_integ_cust', 'email': cust_email, 'password': 'Password123!'}})
assert r1.status_code == 200, f'User registration failed: {{r1.text}}'
user_id = r1.json()['data']['id']
print(f'[1/4 USER SERVICE] Registered test user ID: {{user_id}} ({{cust_email}})')

login_res = u_client.post('/user/login', json={{'email': cust_email, 'password': 'Password123!'}})
assert login_res.status_code == 200
user_token = login_res.json()['data']['access_token']
user_auth = {{'Authorization': f'Bearer {{user_token}}'}}

# --- 2. OPERATION SERVICE ---
for k in list(sys.modules.keys()):
    if k == 'app' or k.startswith('app.'):
        del sys.modules[k]
sys.path = [str(root / 'operation_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
os.environ['DATABASE_URL'] = r'{o_db_url}'

from app.main import app as o_app
from app.core.database import Base as OBase, SessionLocal as OSession, engine as o_engine
OBase.metadata.drop_all(bind=o_engine)
OBase.metadata.create_all(bind=o_engine)
o_client = TestClient(o_app)
admin_auth = auth_header(sub=1, role='admin')

cat_res = o_client.post('/category', headers=admin_auth, json={{'name': '[TEST] Coffee', 'description': 'Fresh brew'}})
assert cat_res.status_code == 200, f'Category creation failed: {{cat_res.text}}'
cat_id = cat_res.json()['data']['id']

prod_res = o_client.post('/product', headers=admin_auth, json={{
    'name': '[TEST] House Cappuccino',
    'description': 'Espresso with steamed milk',
    'price': '4.25',
    'initial_stock': 20,
    'category_id': cat_id,
}})
assert prod_res.status_code == 200, f'Product creation failed: {{prod_res.text}}'
prod_id = prod_res.json()['data']['id']
print(f'[2/4 OPERATION SERVICE] Created product ID: {{prod_id}} with initial stock 20')

order_res = o_client.post('/order', headers=user_auth, json={{'items': [{{'product_id': prod_id, 'quantity': 2}}]}})
assert order_res.status_code == 200, f'Order creation failed: {{order_res.text}}'
order_id = order_res.json()['data']['id']
print(f'[2/4 OPERATION SERVICE] Created order ID: {{order_id}} for user {{user_id}}')

pay_res = o_client.post(f'/order/{{order_id}}/payment', headers=user_auth, json={{'method': 'card'}})
assert pay_res.status_code == 503
assert 'not configured' in pay_res.json()['message']
print(f'[2/4 OPERATION SERVICE] Order {{order_id}} direct payment returned 503 (gateway unconfigured)')

staff_auth = auth_header(sub=2, role='staff')
status_res = o_client.patch(f'/order/{{order_id}}/status', headers=staff_auth, json={{'status': 'paid'}})
assert status_res.status_code == 200
print(f'[2/4 OPERATION SERVICE] Order {{order_id}} advanced to paid by staff')

# --- 3. REPORT SERVICE ---
for k in list(sys.modules.keys()):
    if k == 'app' or k.startswith('app.'):
        del sys.modules[k]
sys.path = [str(root / 'report_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
os.environ['DATABASE_URL'] = r'{r_db_url}'

from app.main import app as r_app
from app.core.database import Base as RBase, SessionLocal as RSession, engine as r_engine
from app.models.snapshot import UserSnapshot, OrderSnapshot
from app.services.sync_service import SyncService
RBase.metadata.drop_all(bind=r_engine)
RBase.metadata.create_all(bind=r_engine)
from app.main import app as r_app
r_client = TestClient(r_app)

with respx.mock(assert_all_called=False) as rmock:
    rmock.get(path='/user').mock(
        side_effect=lambda req: Response(200, content=u_client.get(req.url.path, params=dict(req.url.params), headers=dict(req.headers)).content)
    )
    rmock.get(path='/product').mock(
        side_effect=lambda req: Response(200, content=o_client.get(req.url.path, params=dict(req.url.params), headers=dict(req.headers)).content)
    )
    rmock.get(path='/order').mock(
        side_effect=lambda req: Response(200, content=o_client.get(req.url.path, params=dict(req.url.params), headers=dict(req.headers)).content)
    )

    r_db_sess = RSession()
    sync_result = SyncService().sync_all(r_db_sess)
    print(f'[3/4 REPORT SERVICE] Sync completed: {{sync_result}}')

    summary = r_client.get('/report/sales-summary', headers=admin_auth).json()['data']
    print(f'[3/4 REPORT SERVICE] Sales summary verified: total_orders={{summary[\"total_orders\"]}}, revenue={{summary[\"total_revenue\"]}}')
    assert summary['total_orders'] == 1
    assert summary['paid_orders'] == 1
    assert Decimal(str(summary['total_revenue'])) == Decimal('8.50')

    # --- 4. PURGE AND CASCADE VERIFICATION ---
    print('[4/4 CASCADE VERIFICATION] Purging test user and order from upstream services...')

    # 1) In operation_service:
    for k in list(sys.modules.keys()):
        if k == 'app' or k.startswith('app.'):
            del sys.modules[k]
    sys.path = [str(root / 'operation_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
    os.environ['DATABASE_URL'] = r'{o_db_url}'
    from app.core.database import SessionLocal as OSessionDel
    from app.models.payment import Payment as OpPaymentDel
    from app.models.order import Order as OpOrderDel
    o_del_db = OSessionDel()
    o_del_db.query(OpOrderDel).filter(OpOrderDel.id == order_id).delete()
    o_del_db.commit()
    o_del_db.close()

    # 2) In user_service:
    for k in list(sys.modules.keys()):
        if k == 'app' or k.startswith('app.'):
            del sys.modules[k]
    sys.path = [str(root / 'user_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
    os.environ['DATABASE_URL'] = r'{u_db_url}'
    from app.core.database import SessionLocal as USessionDel
    from app.models.user import User as UsUserDel
    u_del_db = USessionDel()
    u_del_db.query(UsUserDel).filter(UsUserDel.id == user_id).delete()
    u_del_db.commit()
    u_del_db.close()

    # 3) Resync in report_service:
    for k in list(sys.modules.keys()):
        if k == 'app' or k.startswith('app.'):
            del sys.modules[k]
    sys.path = [str(root / 'report_service')] + [p for p in sys.path if not any(p.endswith(x) for x in ['user_service', 'operation_service', 'report_service'])]
    os.environ['DATABASE_URL'] = r'{r_db_url}'
    from app.core.database import SessionLocal as RSessionResync
    from app.models.snapshot import UserSnapshot as USnap, OrderSnapshot as OSnap
    from app.services.sync_service import SyncService as ResyncService
    r_resync_db = RSessionResync()
    resync_out = ResyncService().sync_all(r_resync_db)
    print(f'[4/4 CASCADE VERIFICATION] Stale-removal sync result: {{resync_out}}')
    assert resync_out['stale_users_removed'] == 1
    assert resync_out['stale_orders_removed'] == 1

    # Verify report snapshots have 0 remaining records pointing to deleted IDs
    assert r_resync_db.query(USnap).filter(USnap.user_id == user_id).first() is None
    assert r_resync_db.query(OSnap).filter(OSnap.order_id == order_id).first() is None
    print('[SUCCESS] All snapshots verified clean: 0 orphaned or stale references remain.')
    r_resync_db.close()
"""
    res = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        err_lines = [
            line
            for line in res.stderr.splitlines()
            if "StarletteDeprecationWarning" not in line and "httpx2" not in line
        ]
        if err_lines:
            print("\n".join(err_lines), file=sys.stderr)
    return res.returncode


if __name__ == "__main__":
    sys.exit(run_integration_check())
