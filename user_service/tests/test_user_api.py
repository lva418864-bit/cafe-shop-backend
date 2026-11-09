from coffee_testing import auth_header, marked_email


def test_register_user(client):
    email = marked_email("new_user")
    res = client.post(
        "/user",
        json={
            "username": "test_new_user",
            "email": email,
            "password": "Password123!",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["data"]["email"] == email
    assert data["data"]["role"] == "customer"


def test_login_user(client):
    email = marked_email("login_user")
    password = "Password123!"
    client.post(
        "/user",
        json={"username": "test_login", "email": email, "password": password},
    )

    res = client.post("/user/login", json={"email": email, "password": password})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert "access_token" in data["data"]


def test_get_me(client):
    email = marked_email("me_user")
    reg = client.post(
        "/user",
        json={"username": "test_me", "email": email, "password": "Password123!"},
    )
    user_id = reg.json()["data"]["id"]

    headers = auth_header(sub=user_id, role="customer", email=email)
    res = client.get("/user/me", headers=headers)
    assert res.status_code == 200
    assert res.json()["data"]["id"] == user_id


def test_admin_list_users_forbidden_for_customer(client):
    email = marked_email("customer_for_list")
    reg = client.post(
        "/user",
        json={"username": "test_cust", "email": email, "password": "Password123!"},
    )
    user_id = reg.json()["data"]["id"]

    headers = auth_header(sub=user_id, role="customer", email=email)
    res = client.get("/user", headers=headers)
    assert res.status_code == 403


def test_admin_list_users_allowed_for_admin(client, db_session):
    from app.core.security import hash_password
    from app.models.user import User

    admin_email = marked_email("admin_user")
    admin = User(
        username="test_admin",
        email=admin_email,
        password=hash_password("Password123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)

    headers = auth_header(sub=admin.id, role="admin", email=admin.email)
    res = client.get("/user", headers=headers)
    assert res.status_code == 200
    assert res.json()["success"] is True
