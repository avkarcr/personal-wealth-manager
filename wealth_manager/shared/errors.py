class EntityNotFound(Exception):
    """A referenced object no longer exists."""


class RentalFlowProtected(Exception):
    def __init__(self, account_id):
        self.account_id = account_id
        super().__init__("Edit the rent payment instead of its generated cashflow")
