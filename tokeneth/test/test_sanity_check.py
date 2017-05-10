import asyncio
import random

from tornado.testing import gen_test
from datetime import datetime, timedelta
from tokenservices.ethereum.tx import decode_transaction
from tokenservices.ethereum.utils import data_encoder

from tokeneth.test.base import requires_full_stack, EthServiceBaseTest

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)
from tokenservices.test.ethereum.parity import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS

class TestSanityChecker(EthServiceBaseTest):

    @gen_test(timeout=30)
    @requires_full_stack(manager=True)
    async def test_missing_txs(self, *, manager):

        values = []
        for i in range(10):
            tx = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, random.randint(1, 10) ** 18, i)
            tx = decode_transaction(tx)
            values.append((data_encoder(tx.hash), FAUCET_ADDRESS, data_encoder(tx.to), tx.value, -1, datetime.utcnow() - timedelta(hours=1)))

        async with self.pool.acquire() as con:
            await con.executemany(
                "INSERT INTO transactions (hash, from_address, to_address, value, nonce, created, status) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'unconfirmed')",
                values)
            unconf_count = await con.fetchval("SELECT COUNT(*) FROM transactions WHERE status = 'unconfirmed'")
        self.assertEqual(unconf_count, 10)

        await manager.task_listener.call_task('sanity_check', 0)

        await asyncio.sleep(1)

        async with self.pool.acquire() as con:
            unconf_count = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE status = 'unconfirmed' OR status = 'queued' OR status IS NULL")
            error_count = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE status = 'error'")

        self.assertEqual(unconf_count['count'], 0)
        self.assertEqual(error_count['count'], 10)
