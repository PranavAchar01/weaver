def test_read_proof(client):
    res = client.get("/proofs/poster.txt")
    assert res.status_code == 200
    assert b"bleed 3mm" in res.data


def test_missing_proof(client):
    assert client.get("/proofs/nope.txt").status_code == 404


def test_linecount(client):
    res = client.get("/tools/linecount", query_string={"name": "poster.txt"})
    assert res.get_json()["lines"] == 3
