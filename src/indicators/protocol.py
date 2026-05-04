from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class IndicatorProtocol(Protocol):
    name: str
    inputs: list[str]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        ...
