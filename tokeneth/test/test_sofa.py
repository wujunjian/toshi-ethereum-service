import asyncio
from tornado.escape import json_decode
from tornado.testing import gen_test

from tokeneth.app import urls
from tokenservices.test.base import AsyncHandlerTest
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity, FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokenbrowser.sofa import parse_sofa_message
from ethutils import data_decoder
from tokenbrowser.tx import sign_transaction

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

TEST_PRIVATE_KEY_2 = data_decoder("0x0ffdb88a7a0a40831ca0b19bd31f3f6085764ef8b7db1bd6b57072e5eaea24ff")
TEST_ADDRESS_2 = "0x35351b44e03ec8515664a955146bf9c6e503a381"

class TransactionTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    async def wait_on_tx_confirmation(self, tx_hash, interval_check_callback=None):
        while True:
            resp = await self.fetch("/tx/{}".format(tx_hash))
            self.assertEqual(resp.code, 200)
            body = json_decode(resp.body)
            if body is None or body['blockNumber'] is None:
                if interval_check_callback:
                    f = interval_check_callback()
                    if asyncio.iscoroutine(f):
                        await f
                await asyncio.sleep(1)
            else:
                return body

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_get_sofa_payment(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)
        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)
        tx = sign_transaction(body['tx'], FAUCET_PRIVATE_KEY)
        resp = await self.fetch("/tx", method="POST", body={
            "tx": tx
        })
        self.assertEqual(resp.code, 200, resp.body)
        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        await self.wait_on_tx_confirmation(tx_hash)

        resp = await self.fetch("/tx/{}?format=sofa".format(tx_hash), method="GET")
        self.assertEqual(resp.code, 200, resp.body)

        message = parse_sofa_message(resp.body.decode('utf-8'))
        self.assertEqual(message["txHash"], tx_hash)
        self.assertEqual(message["status"], "confirmed")
