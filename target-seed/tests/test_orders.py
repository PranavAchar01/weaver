def test_orders_for_customer(client):
    rows = client.get("/orders", query_string={"customer": "ada"}).get_json()
    assert sorted(r["item"] for r in rows) == ["flyers", "posters"]


def test_orders_unknown_customer(client):
    assert client.get("/orders", query_string={"customer": "zed"}).get_json() == []
