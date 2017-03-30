import random

from tornado.testing import gen_test
from tokenservices.test.database import requires_database
from tokenservices.test.redis import requires_redis
from tokenservices.test.ethereum.parity import requires_parity
from tokenservices.test.ethereum.faucet import FaucetMixin
from datetime import datetime, timedelta
from tokenservices.ethereum.tx import decode_transaction
from tokenservices.ethereum.utils import data_encoder

from tokeneth.test.base import requires_block_monitor, EthServiceBaseTest

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)
from tokenservices.test.ethereum.parity import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokeneth.test.test_block_monitor import MockPushClientBlockMonitor

class TestSanityChecker(FaucetMixin, EthServiceBaseTest):

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_missing_txs(self, *, ethminer, parity, monitor):

        values = []
        for i in range(10):
            tx = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, random.randint(1, 10) ** 18, i)
            tx = decode_transaction(tx)
            values.append((data_encoder(tx.hash), FAUCET_ADDRESS, data_encoder(tx.to), tx.value, -1, datetime.utcnow() - timedelta(hours=1)))

        async with self.pool.acquire() as con:
            await con.executemany(
                "INSERT INTO transactions (transaction_hash, from_address, to_address, value, nonce, created) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                values)
            count = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE (last_status != 'error' OR last_status IS NULL)")
        self.assertEqual(count['count'], 10)

        await monitor.sanity_check()

        async with self.pool.acquire() as con:
            unconf_count = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE last_status = $1 OR last_status IS NULL", 'unconfirmed')
            error_count = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE last_status = $1", 'error')

        self.assertEqual(unconf_count['count'], 0)
        self.assertEqual(error_count['count'], 10)
