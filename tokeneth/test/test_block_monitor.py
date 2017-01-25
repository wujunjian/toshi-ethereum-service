import unittest
import asyncio
import tokeneth.monitor

from asyncbb.test.base import AsyncHandlerTest
from tokeneth.app import urls
from tornado.escape import json_decode
from tornado.testing import gen_test
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity
from asyncbb.ethereum.test.faucet import FaucetMixin
from tokenbrowser.tx import sign_transaction

from tokeneth.test.test_transaction import TEST_PRIVATE_KEY, TEST_ADDRESS

class BlockMonitor(tokeneth.monitor.BlockMonitor):

    def __init__(self, *args, **kwargs):

        super(BlockMonitor, self).__init__(*args, **kwargs)

        self.confirmation_queue = asyncio.Queue()

    async def send_transaction_notifications(self, transaction):
        self.confirmation_queue.put_nowait(transaction)

class BalanceTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def fetch(self, url, **kwargs):
        return super(BalanceTest, self).fetch("/v1{}".format(url), **kwargs)

    @unittest.skip("TODO: figure out issues with this randomly failing")
    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_get_block_confirmation(self):

        monitor = BlockMonitor(self._app.connection_pool, self._app.config['ethereum']['url'])
        await asyncio.sleep(0.1)
        self._app.monitor = monitor

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(TEST_ADDRESS, val * 10)
        tx = await monitor.confirmation_queue.get()

        body = {
            "from": TEST_ADDRESS,
            "to": addr,
            "value": val
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)

        tx = sign_transaction(body['tx'], TEST_PRIVATE_KEY)

        body = {
            "tx": tx
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        # wait until the transaction is confirmed
        got_unconfirmed = 0
        while True:
            tx = await monitor.confirmation_queue.get()
            if tx['hash'] == tx_hash:
                if tx['blockNumber'] is None:
                    got_unconfirmed += 1
                else:
                    break

        # make sure we got an unconfirmed tx notification
        self.assertEqual(got_unconfirmed, 1)

        await monitor.shutdown()
