"""Account routes — broker-account breakout + selectable sleeve keys."""
from __future__ import annotations

from fastapi import APIRouter

from api.models import (
    AccountKeysResponse,
    BrokerAccountResponse,
    BrokerAccountsResponse,
    UnmappedSleeveResponse,
)
from broker_accounts import (
    load_broker_accounts,
    selectable_account_keys,
    unmapped_sleeves,
)


def make_accounts_router(config_loader) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/accounts/broker", response_model=BrokerAccountsResponse)
    def broker_accounts():
        """Real broker accounts (from local config + balance snapshots) plus
        configured sleeves not funded by any of them. Empty when the user
        config has no broker_accounts block — the panel hides itself."""
        config = config_loader()
        accounts = load_broker_accounts(config)
        return BrokerAccountsResponse(
            accounts=[BrokerAccountResponse(**vars(a)) for a in accounts],
            unmapped_sleeves=[
                UnmappedSleeveResponse(**vars(s))
                for s in unmapped_sleeves(config, accounts)
            ],
        )

    @router.get("/api/v1/accounts/keys", response_model=AccountKeysResponse)
    def account_keys():
        """Sleeve keys for new-position / kill-sheet dropdowns. Single source
        of truth: the config accounts, minus pool members."""
        return AccountKeysResponse(keys=selectable_account_keys(config_loader()))

    return router
