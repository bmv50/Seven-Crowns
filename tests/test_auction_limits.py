"""Low-level auction guards, caps and exact integer commission."""
import asyncio
import runpy

from engine import econ_tx


async def run():
    mock = runpy.run_path('tests/test_econ_tx.py')
    conn, cf = mock['build']({1: dict(gold=10**17, inventory=['item'] * 12),
                            2: dict(gold=10**17, inventory=[])})
    for price in (0, -1, 2**63):
        assert not (await econ_tx.list_lot(cf, 1, 'item', price, f'bad:{price}', f'bad:{price}'))[0]
    assert conn.characters[1]['inventory'] == ['item'] * 12 and not conn.lots
    for i in range(10):
        assert (await econ_tx.list_lot(cf, 1, 'item', 10**16 + 101, str(i), f'list:{i}'))[0]
    assert not (await econ_tx.list_lot(cf, 1, 'item', 100, 'eleventh', 'eleventh'))[0]
    result = await econ_tx.buy_lot(cf, 2, '0', 'buy')
    assert result[0] and result[4]['proceeds'] == (10**16 + 101) * 95 // 100
    assert sum(row['gold_delta'] for op, row in conn.ledger.items() if op.startswith('buy')) == 0
    assert (await econ_tx.cancel_lot(cf, 1, '1', 'cancel'))[0]
    assert (await econ_tx.list_lot(cf, 1, 'item', 100, 'replacement', 'replacement'))[0]
    # A different event colliding on a lot ID is not a successful replay.
    assert not (await econ_tx.list_lot(cf, 1, 'item', 100, 'replacement', 'collision'))[0]
    conn.characters[1]['deleted_at'] = 1
    before = list(conn.characters[1]['inventory'])
    assert not (await econ_tx.list_lot(cf, 1, 'item', 100, 'deleted', 'deleted'))[0]
    assert not (await econ_tx.cancel_lot(cf, 1, 'replacement', 'deleted:cancel'))[0]
    assert not (await econ_tx.buy_lot(cf, 2, 'replacement', 'deleted:sale'))[0]
    assert conn.characters[1]['inventory'] == before


if __name__ == '__main__':
    asyncio.run(run())
    print('OK: auction positive prices, lot cap, exact commission, conflict and deleted-hero guards')
