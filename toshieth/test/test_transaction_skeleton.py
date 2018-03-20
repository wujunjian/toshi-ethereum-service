import asyncio
import os
from tornado.testing import gen_test
from tornado.escape import json_decode
from toshieth.test.base import EthServiceBaseTest, requires_full_stack
from toshi.ethereum.utils import private_key_to_address, data_decoder
from toshi.utils import parse_int
from toshi.test.ethereum.faucet import FAUCET_PRIVATE_KEY

from toshi.ethereum.contract import Contract

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = private_key_to_address(TEST_PRIVATE_KEY)

TEST_PRIVATE_KEY_2 = data_decoder("0x0ffdb88a7a0a40831ca0b19bd31f3f6085764ef8b7db1bd6b57072e5eaea24ff")
TEST_ADDRESS_2 = private_key_to_address(TEST_PRIVATE_KEY_2)

TEST_PRIVATE_KEY_3 = data_decoder("0x46e2301e4af216b64479ec6661814276fe7ac1812b1d463762d86df39a8f50dd")
TEST_ADDRESS_3 = private_key_to_address(TEST_PRIVATE_KEY_3)

TEST_PRIVATE_KEY_4 = data_decoder("0x97838fae42fe69f2fb5e34496cfa8d4c36a8f7c92a0871535302e62dda37f6ff")
TEST_ADDRESS_4 = private_key_to_address(TEST_PRIVATE_KEY_4)

SPLITTER_CONTRACT = open(os.path.join(os.path.dirname(__file__), "splitter.sol")).read()

class TransactionSkeletonTest(EthServiceBaseTest):

    @gen_test(timeout=30)
    @requires_full_stack(block_monitor=True)
    async def test_create_and_send_transaction_with_max_value(self, *, monitor):

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)

        resp = await self.fetch("/tx/skel", method="POST", body={
            "from": TEST_ADDRESS,
            "to": TEST_ADDRESS_2,
            "value": "max"

        })
        self.assertEqual(resp.code, 200)
        body = json_decode(resp.body)
        tx_hash = await self.sign_and_send_tx(TEST_PRIVATE_KEY, body['tx'])
        await self.wait_on_tx_confirmation(tx_hash)

        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        print(data)
        self.assertEqual(parse_int(data['confirmed_balance']), 0)
        self.assertEqual(parse_int(data['unconfirmed_balance']), 0)

    @gen_test(timeout=30)
    @requires_full_stack(block_monitor=True)
    async def test_create_and_send_transaction_with_max_value_with_pending_balance(self, *, monitor):

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)

        tx_hash_1 = await self.send_tx(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 5 * 10 ** 18)

        resp = await self.fetch("/tx/skel", method="POST", body={
            "from": TEST_ADDRESS,
            "to": TEST_ADDRESS_2,
            "value": "max"

        })
        self.assertEqual(resp.code, 200)
        body = json_decode(resp.body)
        tx_hash_2 = await self.sign_and_send_tx(TEST_PRIVATE_KEY, body['tx'])
        await self.wait_on_tx_confirmation(tx_hash_1)
        await self.wait_on_tx_confirmation(tx_hash_2)

        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), 0)
        self.assertEqual(parse_int(data['unconfirmed_balance']), 0)

    @gen_test(timeout=30)
    @requires_full_stack(block_monitor=True, parity=True)
    async def test_create_and_send_transaction_with_max_value_to_contract(self, *, monitor, parity):

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']
        contract = await Contract.from_source_code(
            SPLITTER_CONTRACT.encode('utf-8'), "Splitter",
            constructor_data=[[TEST_ADDRESS, TEST_ADDRESS_2, TEST_ADDRESS_3, TEST_ADDRESS_4]],
            deployer_private_key=FAUCET_PRIVATE_KEY)

        await self.faucet(TEST_ADDRESS, 9 * 10 ** 18)

        resp = await self.fetch("/tx/skel", method="POST", body={
            "from": TEST_ADDRESS,
            "to": contract.address,
            "value": "max"

        })
        self.assertEqual(resp.code, 200)
        body = json_decode(resp.body)
        print(body)
        tx_hash = await self.sign_and_send_tx(TEST_PRIVATE_KEY, body['tx'])
        await self.wait_on_tx_confirmation(tx_hash)

        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), 0)
        self.assertEqual(parse_int(data['unconfirmed_balance']), 0)

        tx_hash = await contract.withdraw.set_sender(FAUCET_PRIVATE_KEY)()
        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS_2))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        expected_balance = parse_int(data['confirmed_balance'])
        print(expected_balance)
        self.assertNotEqual(expected_balance, 0)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS_2))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), expected_balance)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS_3))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), expected_balance)

        resp = await self.fetch('/balance/{}'.format(TEST_ADDRESS_4))
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), expected_balance)
