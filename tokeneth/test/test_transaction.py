import asyncio
import time
from tornado.escape import json_decode, json_encode
from tornado.testing import gen_test
from tornado.platform.asyncio import to_asyncio_future

from tokeneth.app import urls
from tokeneth.test.base import EthServiceBaseTest
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity, FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokenbrowser.request import sign_request
from ethutils import data_decoder, data_encoder
from tokenbrowser.tx import sign_transaction, decode_transaction, signature_from_transaction

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

TEST_PRIVATE_KEY_2 = data_decoder("0x0ffdb88a7a0a40831ca0b19bd31f3f6085764ef8b7db1bd6b57072e5eaea24ff")
TEST_ADDRESS_2 = "0x35351b44e03ec8515664a955146bf9c6e503a381"

class TransactionTest(EthServiceBaseTest):

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

        tx = decode_transaction(tx)
        self.assertEqual(tx_hash, data_encoder(tx.hash))

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM transactions WHERE nonce = $1", tx.nonce)
        self.assertEqual(len(rows), 1)

        await self.wait_on_tx_confirmation(tx_hash)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_create_and_send_transaction_with_separate_sig(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)
        tx = decode_transaction(body['tx'])
        tx = sign_transaction(tx, FAUCET_PRIVATE_KEY)
        sig = signature_from_transaction(tx)

        body = {
            "tx": body['tx'],
            "signature": data_encoder(sig)
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        async def check_db():
            async with self.pool.acquire() as con:
                rows = await con.fetch("SELECT * FROM transactions WHERE nonce = $1", tx.nonce)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['transaction_hash'], tx_hash)

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

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_transactions_with_known_sender_token_id(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        tx = sign_transaction(json_decode(resp.body)['tx'], FAUCET_PRIVATE_KEY)

        body = {
            "tx": tx
        }

        resp = await self.fetch_signed("/tx", signing_key=TEST_PRIVATE_KEY_2, method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        tx_hash = json_decode(resp.body)['tx_hash']

        async with self.pool.acquire() as con:

            row = await con.fetch("SELECT * FROM transactions WHERE sender_token_id = $1", FAUCET_ADDRESS)

            self.assertEqual(len(row), 0)

            row = await con.fetch("SELECT * FROM transactions WHERE sender_token_id = $1", TEST_ADDRESS_2)

            self.assertEqual(len(row), 1)
            self.assertEqual(row[0]['from_address'], FAUCET_ADDRESS)

        await self.wait_on_tx_confirmation(tx_hash)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_transactions_with_known_sender_token_id_but_invalid_signature(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        tx = sign_transaction(json_decode(resp.body)['tx'], FAUCET_PRIVATE_KEY)

        body = {
            "tx": tx
        }

        timestamp = int(time.time())
        signature = sign_request(FAUCET_PRIVATE_KEY, "POST", "/v1/tx", timestamp, json_encode(body).encode('utf-8'))

        resp = await self.fetch_signed("/tx", method="POST", body=body,
                                       address=TEST_ADDRESS_2, signature=signature, timestamp=timestamp)

        self.assertEqual(resp.code, 400, resp.body)
        self.assertIsNotNone(resp.body)
        error = json_decode(resp.body)
        self.assertIn('errors', error)
        self.assertEqual(len(error['errors']), 1)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_create_and_send_transaction_with_data(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 10 ** 10,
            "data": "0xffffffff"
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
    async def test_create_and_send_transaction_with_0_value_and_data(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": 0,
            "data": "0xffffffff"
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
    async def test_create_and_send_transaction_with_no_value_and_data(self):

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "data": "0xffffffff"
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
    async def test_create_transaction_with_large_data(self):

        body = {
            "from": "0x0004DE837Ea93edbE51c093f45212AB22b4B35fc",
            "to": "0xa0c4d49fe1a00eb5ee3d85dc7a287d84d8c66699",
            "value": 0,
            "data": "0x94d9cf8f00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000080000000000000000000000000000000000000000000000000000000000000003c00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_transaction_nonce_lock(self):
        """Spams transactions with the same nonce, and ensures the server rejects all but one"""

        no_tests = 20

        txs = []
        tx = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 10)
        dtx = decode_transaction(tx)
        txs.append(sign_transaction(tx, FAUCET_PRIVATE_KEY))
        for i in range(11, 10 + no_tests):
            tx = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** i)
            self.assertEqual(decode_transaction(tx).nonce, dtx.nonce)
            txs.append(sign_transaction(tx, FAUCET_PRIVATE_KEY))

        responses = await asyncio.gather(*(to_asyncio_future(self.fetch("/tx", method="POST", body={"tx": tx})) for tx in txs))

        ok = 0
        bad = 0
        for resp in responses:
            if resp.code == 200:
                ok += 1
            else:
                bad += 1
        self.assertEqual(ok, 1)
        self.assertEqual(bad, no_tests - 1)
