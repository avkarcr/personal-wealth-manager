"""SQLite adapters for existing tables. No commits: UnitOfWork owns durability."""
from dataclasses import asdict, replace
from wealth_manager.modules.planning.domain import PlanningError

from wealth_manager.shared.errors import EntityNotFound
from wealth_manager.modules.positions.domain import Position
from wealth_manager.modules.cashflows.domain import Cashflow
from wealth_manager.modules.rentals.domain import Lease, RentPayment
from wealth_manager.modules.valuation.domain import ExchangeRates


def get_row(connection, table, record_id):
    # table is supplied only by repository code, never by a request.
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
    if row is None:
        raise EntityNotFound(record_id)
    return dict(row)


def save_row(connection, table, record):
    data = asdict(record)
    record_id = data.pop('id')
    columns = tuple(data)
    if record_id is not None:
        get_row(connection, table, record_id)
        connection.execute(f"UPDATE {table} SET " + ','.join(f'{c}=?' for c in columns) + ' WHERE id=?',
                           tuple(data.values()) + (record_id,))
        return record_id
    return connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                              tuple(data.values())).lastrowid


class SqlitePositionRepository:
    def __init__(self, connection): self.connection = connection

    def get(self, record_id):
        return Position(**get_row(self.connection, 'holdings', record_id))

    def save(self, position):
        if position.quantity < 0 and self.connection.execute('SELECT 1 FROM allocations WHERE holding_id=?', (position.id,)).fetchone():
            raise PlanningError('Сначала удалите распределение актива из портфелей')
        return save_row(self.connection, 'holdings', position)

    def delete(self, record_id):
        self.get(record_id)
        if self.connection.execute('SELECT 1 FROM allocations WHERE holding_id=?', (record_id,)).fetchone():
            raise PlanningError('Сначала удалите распределение актива из портфелей')
        self.connection.execute('DELETE FROM holdings WHERE id=?', (record_id,))

    def set_price(self, record_id, account_id, price, updated_at):
        self.connection.execute('UPDATE holdings SET price=?,updated_at=? WHERE id=? AND account_id=?',
                                (price, updated_at, record_id, account_id))


class SqliteCashflowRepository:
    def __init__(self, connection): self.connection = connection

    def get(self, record_id):
        return Cashflow(**get_row(self.connection, 'cashflows', record_id))

    def save(self, flow):
        return save_row(self.connection, 'cashflows', flow)

    def delete(self, record_id):
        self.get(record_id)
        self.connection.execute('DELETE FROM cashflows WHERE id=?', (record_id,))

    def sync_rent(self, payment_id, flow):
        row = self.connection.execute('SELECT id FROM cashflows WHERE rent_payment_id=?', (payment_id,)).fetchone()
        if flow is None:
            self.connection.execute('DELETE FROM cashflows WHERE rent_payment_id=?', (payment_id,))
        else:
            self.save(replace(flow, id=row[0] if row else None, rent_payment_id=payment_id))

    def through(self, account_id, day):
        return [Cashflow(**dict(row)) for row in self.connection.execute(
            'SELECT * FROM cashflows WHERE account_id=? AND date<=?', (account_id, day))]


class SqliteRentalRepository:
    def __init__(self, connection): self.connection = connection

    def get(self, record_id):
        return Lease(**get_row(self.connection, 'leases', record_id))

    def save(self, lease):
        return save_row(self.connection, 'leases', lease)

    def payments(self, lease_id):
        return [RentPayment(**dict(row)) for row in self.connection.execute(
            'SELECT * FROM rent_payments WHERE lease_id=? ORDER BY period_start,id', (lease_id,))]

    def get_payment(self, record_id):
        return RentPayment(**get_row(self.connection, 'rent_payments', record_id))

    def find_payment(self, record_id):
        row = self.connection.execute('SELECT * FROM rent_payments WHERE id=?', (record_id,)).fetchone()
        return RentPayment(**dict(row)) if row else None

    def save_payment(self, payment):
        return save_row(self.connection, 'rent_payments', payment)

    def delete_payment(self, record_id):
        self.connection.execute('DELETE FROM rent_payments WHERE id=?', (record_id,))

    def delete(self, record_id):
        self.connection.execute('DELETE FROM leases WHERE id=?', (record_id,))


class SqliteValuationRepository:
    def __init__(self, connection): self.connection = connection

    def rates(self):
        row = self.connection.execute("SELECT value FROM settings WHERE key='base_currency'").fetchone()
        return ExchangeRates(dict(self.connection.execute('SELECT currency,rate FROM rates')),
                             row[0] if row else 'RUB')

    def save_history(self, account_id, day, value_rub, invested_rub):
        self.connection.execute(
            'INSERT INTO snapshots(date,account_id,value_rub,invested_rub) VALUES (?,?,?,?) '
            'ON CONFLICT(date,account_id) DO UPDATE SET value_rub=excluded.value_rub,invested_rub=excluded.invested_rub',
            (day, account_id, value_rub, invested_rub))
