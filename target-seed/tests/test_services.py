from services.config_loader import load_config
from services.hashing import check_password, hash_password
from services.pricing import quote
from services.session import dump_session, load_session
from utils.dates import due_date
from utils.text import slugify


def test_config_roundtrip():
    assert load_config("shop: Ledgerline\nrush_fee: 15\n") == {"shop": "Ledgerline", "rush_fee": 15}


def test_empty_config():
    assert load_config("") == {}


def test_session_roundtrip():
    data = {"user": "ada", "cart": ["posters", "cards"], "qty": 3}
    assert load_session(dump_session(data)) == data


def test_password_roundtrip():
    digest = hash_password("correct horse")
    assert check_password("correct horse", digest)
    assert not check_password("wrong", digest)
    assert digest != "correct horse"


def test_quote():
    assert quote("posters", 10) == 40.0
    assert quote("cards", 100, rush=True) == 35.0


def test_helpers():
    assert slugify("Rush Order") == "rush-order"
    assert due_date(3).toordinal() - due_date(0).toordinal() == 3
