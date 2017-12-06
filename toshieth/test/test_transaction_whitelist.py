import asyncio
import time
from tornado.escape import json_decode, json_encode
from tornado.testing import gen_test
from tornado.platform.asyncio import to_asyncio_future

from toshieth.test.base import EthServiceBaseTest, requires_task_manager, requires_full_stack
from toshi.test.database import requires_database
from toshi.test.redis import requires_redis
from toshi.test.ethereum.parity import requires_parity, FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from toshi.analytics import encode_id
from toshi.request import sign_request
from toshi.ethereum.utils import data_decoder, data_encoder
from toshi.ethereum.tx import sign_transaction, decode_transaction, signature_from_transaction, encode_transaction, DEFAULT_STARTGAS, DEFAULT_GASPRICE
from toshi.utils import parse_int

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

TEST_PRIVATE_KEY_2 = data_decoder("0x0ffdb88a7a0a40831ca0b19bd31f3f6085764ef8b7db1bd6b57072e5eaea24ff")
TEST_ADDRESS_2 = "0x35351b44e03ec8515664a955146bf9c6e503a381"

class TransactionWhitelistTest(EthServiceBaseTest):

    @gen_test(timeout=15)
    @requires_full_stack
    async def test_gas_price_whitelist(self):

        gas_station_gas_price = 50000000000
        custom_gas_price = 2000000000
        assert(gas_station_gas_price != DEFAULT_GASPRICE)
        assert(custom_gas_price != DEFAULT_GASPRICE)
        assert(gas_station_gas_price != custom_gas_price)
        self.redis.set("gas_station_standard_gas_price", hex(gas_station_gas_price))

        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO from_address_gas_price_whitelist (address) VALUES ($1)", FAUCET_ADDRESS)
            await con.execute("INSERT INTO to_address_gas_price_whitelist (address) VALUES ($1)", TEST_ADDRESS_2)

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ADDRESS,
            "value": hex(10 ** 10),
            "gas_price": hex(custom_gas_price)
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)
        self.assertEqual(resp.code, 200)
        result = json_decode(resp.body)
        self.assertEqual(result['gas_price'], hex(custom_gas_price))

        body['from'] = TEST_ADDRESS
        body['to'] = FAUCET_ADDRESS

        resp = await self.fetch("/tx/skel", method="POST", body=body)
        self.assertEqual(resp.code, 200)
        result = json_decode(resp.body)
        self.assertEqual(result['gas_price'], hex(gas_station_gas_price))

        body['to'] = TEST_ADDRESS_2
        resp = await self.fetch("/tx/skel", method="POST", body=body)
        self.assertEqual(resp.code, 200)
        result = json_decode(resp.body)
        self.assertEqual(result['gas_price'], hex(custom_gas_price))
