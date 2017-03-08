import asyncio
from tokenservices.test.base import AsyncHandlerTest, TokenWebSocketJsonRPCClient
from tokeneth.app import urls
from tornado.testing import gen_test
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity
from asyncbb.ethereum.test.faucet import FaucetMixin
from tokenbrowser.tx import sign_transaction, create_transaction, DEFAULT_STARTGAS, DEFAULT_GASPRICE, encode_transaction

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)

from tokeneth.test.test_block_monitor import requires_block_monitor

class WebsocketTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    async def websocket_connect(self, signing_key):
        con = TokenWebSocketJsonRPCClient(self.get_url("/ws"), signing_key=signing_key)
        await con.connect()
        return con

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor
    async def test_subscriptions(self, *, parity, ethminer):

        val = 761751855997712

        ws_con = await self.websocket_connect(TEST_ID_KEY)
        await ws_con.call("subscribe", [TEST_ID_ADDRESS])

        tx_hash = await self.faucet(TEST_ID_ADDRESS, val)

        result = await ws_con.read()
        self.assertIsNotNone(result)
        self.assertEqual(result['params']['transaction']['hash'], tx_hash)
        ws_con.con.close()

        ws_con = await self.websocket_connect(TEST_ID_KEY)

        await self.faucet(TEST_ID_ADDRESS, val)

        # make sure we don't still get any notifications
        result = await ws_con.read(timeout=1)
        self.assertIsNone(result)

        # make sure subscriptions to a different address from the token id work
        await ws_con.call("subscribe", [TEST_WALLET_ADDRESS])

        tx_hash = await self.faucet(TEST_WALLET_ADDRESS, val)

        result = await ws_con.read()
        self.assertIsNotNone(result)
        self.assertEqual(result['params']['transaction']['hash'], tx_hash)

        await ws_con.call("unsubscribe", [TEST_WALLET_ADDRESS])

        await self.faucet(TEST_WALLET_ADDRESS, val)

        result = await ws_con.read(timeout=1)
        self.assertIsNone(result)

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor
    async def test_list_subscriptions(self):

        ws_con = await self.websocket_connect(TEST_ID_KEY)

        subs = await ws_con.call("list_subscriptions")
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 0)

        await ws_con.call("subscribe", [TEST_ID_ADDRESS])
        subs = await ws_con.call("list_subscriptions")
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0], TEST_ID_ADDRESS)

        await ws_con.call("subscribe", [TEST_ID_ADDRESS, TEST_WALLET_ADDRESS])
        subs = await ws_con.call("list_subscriptions")
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 2)
        self.assertEqual(set(subs), {TEST_ID_ADDRESS, TEST_WALLET_ADDRESS})

        await ws_con.call("unsubscribe", [TEST_ID_ADDRESS])
        subs = await ws_con.call("list_subscriptions")
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0], TEST_WALLET_ADDRESS)

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor
    async def test_send_transaction(self):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(TEST_ID_ADDRESS, val * 10)

        ws_con = await self.websocket_connect(TEST_ID_KEY)

        result = await ws_con.call("create_transaction_skeleton", {
            "from": TEST_ID_ADDRESS,
            "to": addr,
            "value": val
        })
        tx = sign_transaction(result, TEST_ID_KEY)

        tx_hash = await ws_con.call("send_transaction", {
            "tx": tx
        })

        self.assertIsNotNone(tx_hash)

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity(pass_ethminer=True)
    @requires_block_monitor
    async def test_get_balance(self, *, ethminer):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 8761751855997712
        val2 = int(val / 2)

        await self.faucet(TEST_ID_ADDRESS, val)

        ws_con = await self.websocket_connect(TEST_ID_KEY)

        result = await ws_con.call("get_balance", [TEST_ID_ADDRESS])

        self.assertEqual(int(result['confirmed_balance'][2:], 16), val)
        self.assertEqual(int(result['unconfirmed_balance'][2:], 16), val)

        # make sure no blocks get mined for a bit
        ethminer.pause()

        tx = create_transaction(nonce=0x100000, gasprice=DEFAULT_GASPRICE, startgas=DEFAULT_STARTGAS,
                                to=addr, value=val2)
        tx = sign_transaction(tx, TEST_ID_KEY)
        tx = encode_transaction(tx)

        await ws_con.call("subscribe", [addr])

        tx_hash = await ws_con.call("send_transaction", {"tx": tx})

        new_balance = val - (val2 + DEFAULT_STARTGAS * DEFAULT_GASPRICE)

        result = await ws_con.call("get_balance", [TEST_ID_ADDRESS])
        self.assertEqual(int(result['confirmed_balance'][2:], 16), val)
        self.assertEqual(int(result['unconfirmed_balance'][2:], 16), new_balance)

        # check for the unconfirmed notification
        result = await ws_con.read()
        self.assertIsNotNone(result)
        self.assertEqual(result['params']['transaction']['hash'], tx_hash)
        self.assertIsNone(result['params']['transaction']['blockNumber'])

        # restart mining
        ethminer.start()

        result = await ws_con.read()
        self.assertIsNotNone(result)
        self.assertEqual(result['params']['transaction']['hash'], tx_hash)
        self.assertIsNotNone(result['params']['transaction']['blockNumber'])

        result = await ws_con.call("get_balance", [TEST_ID_ADDRESS])

        self.assertEqual(int(result['confirmed_balance'][2:], 16), new_balance,
                         "int('{}', 16) != {}".format(result['confirmed_balance'], new_balance))
        self.assertEqual(int(result['unconfirmed_balance'][2:], 16), new_balance,
                         "int('{}', 16) != {}".format(result['unconfirmed_balance'], new_balance))
