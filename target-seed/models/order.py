from dataclasses import dataclass


@dataclass
class Order:
    id: int
    customer: str
    item: str
    status: str = "open"
