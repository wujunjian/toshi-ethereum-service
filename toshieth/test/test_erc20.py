# -*- coding: utf-8 -*-
import asyncio
import os

from tornado.escape import json_decode
from tornado.testing import gen_test

from toshieth.test.base import EthServiceBaseTest, requires_full_stack
from toshi.test.ethereum.faucet import FAUCET_PRIVATE_KEY
from toshi.sofa import parse_sofa_message
from toshi.ethereum.utils import private_key_to_address, data_decoder

from toshi.ethereum.contract import Contract

ERC20_CONTRACT = open(os.path.join(os.path.dirname(__file__), "erc20.sol")).read()

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_PRIVATE_KEY_2 = data_decoder("0x8945608e66736aceb34a83f94689b4e98af497ffc9dc2004a93824096330fa77")
TEST_ADDRESS = private_key_to_address(TEST_PRIVATE_KEY)
TEST_ADDRESS_2 = private_key_to_address(TEST_PRIVATE_KEY_2)

TEST_APN_ID = "64be4fe95ba967bb533f0c240325942b9e1f881b5cd2982568a305dd4933e0bd"

class ERC20Test(EthServiceBaseTest):

    async def deploy_erc20_contract(self, symbol, name, decimals):
        sourcecode = ERC20_CONTRACT.encode('utf-8')
        contract_name = "Token"
        constructor_data = [2**256 - 1, name, decimals, symbol]
        contract = await Contract.from_source_code(sourcecode, contract_name, constructor_data=constructor_data, deployer_private_key=FAUCET_PRIVATE_KEY)

        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO tokens (contract_address, symbol, name, decimals) VALUES ($1, $2, $3, $4)",
                              contract.address, symbol, name, decimals)

        return contract

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_erc20_balance_update(self, *, parity, push_client, monitor):
        """Tests that on initial PN registration the user's token cache is updated

        Creates 4 erc20 tokens, gives a test address some of 3 of those tokens,
        registeres that address for PNs, and checks that the balance cache is updated
        """

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']
        token_args = [
            ["TST", "Test Token", 18],
            ["BOB", "Big Old Bucks", 10],
            ["HMM", "Hmmmmmm", 5],
            ["NOT", "Not This One", 20]
        ]
        tokens = {}
        contracts = {}

        for args in token_args:
            contract = await self.deploy_erc20_contract(*args)
            contracts[contract.address] = contract
            tokens[contract.address] = {"symbol": args[0], "name": args[1], "decimals": args[2], "contract": contract}
            args.append(contract.address)

        for token in tokens.values():
            if token['symbol'] == token_args[-1][0]:
                continue
            # give "1" of each token (except NOT)
            contract = token['contract']
            await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** token['decimals'])

            result = await contract.balanceOf(TEST_ADDRESS)
            self.assertEquals(result, 10 ** token['decimals'])

        # force block check to clear out txs pre registration
        await monitor.block_check()

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # get user's initial token balance
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), len(token_args) - 1)

        for balance in body['tokens']:
            self.assertEqual(int(balance['value'], 16), 10 ** tokens[balance['contract_address']]['decimals'])

        await self.send_tx(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 18)

        # wait for unconfirmed and confirmed PN, otherwise the contract send will overwrite it (TODO)
        await push_client.get()
        await push_client.get()

        # test that receiving new tokens triggers a PN
        for token in tokens.values():
            contract = token['contract']
            await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** token['decimals'], wait_for_confirmation=False)
            while True:
                pn = await push_client.get()
                sofa = parse_sofa_message(pn[1]['message'])
                if sofa['status'] == 'confirmed':
                    break
            self.assertEqual(sofa['contractAddress'], contract.address)
            self.assertEqual(sofa['value'], hex(10 ** token['decimals']))
            self.assertEqual(sofa['toAddress'], TEST_ADDRESS)
            await asyncio.sleep(0.1)
            async with self.pool.acquire() as con:
                balance = await con.fetchrow("SELECT * FROM token_balances WHERE eth_address = $1 AND contract_address = $2",
                                             TEST_ADDRESS, contract.address)
            self.assertEqual(int(balance['value'], 16), (10 ** token['decimals']) * (2 if token['symbol'] != token_args[-1][0] else 1),
                             "invalid balance after updating {} token".format(token['symbol']))

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_transaction_skeleton_erc20_transfer(self, *, parity, push_client, monitor):
        """Tests that the transaction skeleton endpoint """

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        contract = await self.deploy_erc20_contract("TST", "Test Token", 18)
        await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)

        result = await contract.balanceOf(TEST_ADDRESS)
        self.assertEquals(result, 10 * 10 ** 18)

        # force block check to clear out txs pre registration
        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # make sure tokens are empty to start
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['value'], hex(10 * 10 ** 18))

        # make sure tokens are empty to start
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 0)

        await self.send_tx(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 18)

        # wait for unconfirmed and confirmed PN, otherwise the contract send will overwrite it (TODO)
        await push_client.get()
        await push_client.get()

        # test sending new tokens via skel
        await self.send_tx(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 5 * 10 ** 18, token_address=contract.address)

        # wait for unconfirmed and confirmed PN, otherwise the contract send will overwrite it (TODO)
        await push_client.get()
        await push_client.get()
        # randomly the balance update isn't complete right after the PNs are sent
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['value'], hex(5 * 10 ** 18))

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['value'], hex(5 * 10 ** 18))

        # test sending tokens when balance isn't updated fails
        await self.get_tx_skel(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 10 * 10 ** 18,
                               token_address=contract.address, expected_response_code=400)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_bad_erc20_transaction(self, *, parity, push_client, monitor):
        """Tests that the transaction skeleton endpoint """

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        contract = await self.deploy_erc20_contract("TST", "Test Token", 18)
        await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS, 10 ** 18)

        result = await contract.balanceOf(TEST_ADDRESS)
        self.assertEquals(result, 10 * 10 ** 18)

        # force block check to clear out txs pre registration
        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY_2, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # send transaction sending more tokens than the sender has
        tx_hash = await contract.transfer.set_sender(TEST_PRIVATE_KEY)(TEST_ADDRESS_2, 20 * 10 ** 18, startgas=61530)

        # wait for unconfirmed
        pn = await push_client.get()
        sofa = parse_sofa_message(pn[1]['message'])
        self.assertEqual(sofa['status'], 'unconfirmed')
        self.assertEqual(sofa['value'], hex(20 * 10 ** 18))
        self.assertEqual(sofa['txHash'], tx_hash)
        pn = await push_client.get()
        sofa = parse_sofa_message(pn[1]['message'])
        self.assertEqual(sofa['status'], 'error')
        self.assertEqual(sofa['txHash'], tx_hash)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_erc20_max_transaction(self, *, parity, push_client, monitor):
        """Tests that the transaction skeleton endpoint """

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        contract = await self.deploy_erc20_contract("TST", "Test Token", 18)
        await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS, 10 ** 18)

        result = await contract.balanceOf(TEST_ADDRESS)
        self.assertEquals(result, 10 * 10 ** 18)

        # force block check to clear out txs pre registration
        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY_2, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # send transaction sending more tokens than the sender has
        # test sending new tokens via skel
        await self.send_tx(TEST_PRIVATE_KEY, TEST_ADDRESS_2,
                           "max", token_address=contract.address)

        await push_client.get()
        await push_client.get()

        result = await contract.balanceOf(TEST_ADDRESS)
        self.assertEquals(result, 0)
        result = await contract.balanceOf(TEST_ADDRESS_2)
        self.assertEquals(result, 10 * 10 ** 18)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_newly_added_erc20_token(self, *, parity, push_client, monitor):
        """Tests that the transaction skeleton endpoint """

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        contract = await self.deploy_erc20_contract("TST", "Test Token", 18)
        await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS, 10 ** 18)

        result = await contract.balanceOf(TEST_ADDRESS)
        self.assertEquals(result, 10 * 10 ** 18)

        # force block check to clear out txs pre registration
        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)

        # deploy a new erc20 contract
        contract2 = await self.deploy_erc20_contract("NEW", "New Token", 18)
        await contract2.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)
