def test_find_user_by_name(client):
    rows = client.get("/users", query_string={"name": "ada"}).get_json()
    assert [r["email"] for r in rows] == ["ada@example.com"]


def test_unknown_user_is_empty(client):
    assert client.get("/users", query_string={"name": "nobody"}).get_json() == []


def test_index(client):
    assert b"Ledgerline" in client.get("/").data
