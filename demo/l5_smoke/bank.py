from __future__ import annotations

from numbers import Real


class Account:
    """Represents a simple bank account with validated starting balance."""

    def __init__(self, starting_balance: Real = 0) -> None:
        self._balance = self._validate_starting_balance(starting_balance)

    @staticmethod
    def _validate_starting_balance(value: Real) -> float:
        """Ensure the initial balance is a real, non-negative number."""
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("Starting balance must be a real number")

        if value < 0:
            raise ValueError("Starting balance cannot be negative")

        return float(value)

    @staticmethod
    def _validate_positive_amount(value: Real) -> float:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError("Amount must be a real number")

        if value <= 0:
            raise ValueError("Amount must be greater than zero")

        return float(value)

    @property
    def balance(self) -> float:
        return self._balance

    @balance.setter
    def balance(self, value: float) -> None:  # pragma: no cover - defensive API guard
        raise AttributeError("Balance is read-only; use deposit or withdraw to modify it")

    def deposit(self, amount: Real) -> float:
        value = self._validate_positive_amount(amount)
        self._balance += value
        return self._balance

    def withdraw(self, amount: Real) -> float:
        value = self._validate_positive_amount(amount)
        if value > self._balance:
            raise ValueError("Insufficient funds")
        self._balance -= value
        return self._balance


def transfer(src: Account, dst: Account, amount: Real) -> tuple[float, float]:
    """Atomically move ``amount`` from ``src`` to ``dst`` accounts.

    The function validates the inputs, withdraws the funds from the source, and
    deposits them into the destination. If the withdrawal fails (for example due
    to insufficient funds), no money is deposited, preserving atomicity.

    Returns a tuple of the new ``(src_balance, dst_balance)``.
    """

    if not isinstance(src, Account) or not isinstance(dst, Account):
        raise TypeError("Both src and dst must be Account instances")

    if src is dst:
        raise ValueError("Source and destination accounts must be different")

    value = Account._validate_positive_amount(amount)
    src.withdraw(value)
    dst.deposit(value)
    return src.balance, dst.balance
