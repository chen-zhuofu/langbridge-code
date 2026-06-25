import pytest

from bank import Account, transfer


def test_transfer_moves_funds_successfully():
    src = Account(200)
    dst = Account(25)

    src_balance, dst_balance = transfer(src, dst, 75)

    assert src_balance == pytest.approx(125.0)
    assert dst_balance == pytest.approx(100.0)
    assert src.balance == pytest.approx(125.0)
    assert dst.balance == pytest.approx(100.0)


def test_transfer_fails_on_insufficient_funds_and_preserves_balances():
    src = Account(50)
    dst = Account(10)

    with pytest.raises(ValueError, match="Insufficient funds"):
        transfer(src, dst, 200)

    assert src.balance == pytest.approx(50.0)
    assert dst.balance == pytest.approx(10.0)


def test_transfer_rejects_invalid_amount():
    src = Account(100)
    dst = Account(100)

    with pytest.raises(ValueError):
        transfer(src, dst, 0)


def test_transfer_requires_distinct_accounts():
    account = Account(100)

    with pytest.raises(ValueError):
        transfer(account, account, 10)


def test_transfer_requires_account_instances():
    src = Account(100)

    with pytest.raises(TypeError):
        transfer(src, object(), 10)
