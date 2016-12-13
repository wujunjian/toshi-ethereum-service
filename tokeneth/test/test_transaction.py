import asyncio
from tornado.escape import json_decode
from tornado.testing import gen_test

from tokeneth.app import urls
from asyncbb.test.base import AsyncHandlerTest
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity, FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokenbrowser.utils import data_decoder
from tokenbrowser.tx import sign_transaction

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

class TransactionTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def fetch(self, url, **kwargs):
        return super(TransactionTest, self).fetch("/v1{}".format(url), **kwargs)

    async def wait_on_tx_confirmation(self, tx_hash):
        while True:
            resp = await self.fetch("/tx/{}".format(tx_hash))
            self.assertEqual(resp.code, 200)
            body = json_decode(resp.body)
            if body['tx'] is None or body['tx']['blockNumber'] is None:
                await asyncio.sleep(1)
            else:
                return body['tx']

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_create_and_send_transaction(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)

        tx = sign_transaction(body['tx'], FAUCET_PRIVATE_KEY)

        body = {
            "tx": tx
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        await self.wait_on_tx_confirmation(tx_hash)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_create_and_send_multiple_transactions(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        tx_hashes = []

        for i in range(10):
            resp = await self.fetch("/tx/skel", method="POST", body=body)

            self.assertEqual(resp.code, 200)

            tx = sign_transaction(json_decode(resp.body)['tx'], FAUCET_PRIVATE_KEY)

            resp = await self.fetch("/tx", method="POST", body={
                "tx": tx
            })

            self.assertEqual(resp.code, 200, resp.body)

            tx_hash = json_decode(resp.body)['tx_hash']
            tx_hashes.append(tx_hash)

        for tx_hash in tx_hashes:
            await self.wait_on_tx_confirmation(tx_hash)

    @gen_test
    @requires_parity
    async def test_invalid_transaction(self):

        resp = await self.fetch("/tx/{}".format("0x2f321aa116146a9bc62b61c76508295f708f42d56340c9e613ebfc27e33f240c"))
        self.assertEqual(resp.code, 404)

        resp = await self.fetch("/tx/{}".format("0x2f321aa116146a9bc62b61c7340c9e613ebfc27e33f240c"))
        self.assertEqual(resp.code, 404)
