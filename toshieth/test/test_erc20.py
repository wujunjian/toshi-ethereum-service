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
SIMPLE_EXCHANGE_CONTRACT = open(os.path.join(os.path.dirname(__file__), "simpleexchange.sol")).read()
WETH_CONTRACT = open(os.path.join(os.path.dirname(__file__), "weth9.sol")).read()

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
            # first test PNs from external transactions
            tx_hash = await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** token['decimals'], wait_for_confirmation=False)
            pn = await push_client.get()
            sofa = parse_sofa_message(pn[1]['message'])
            self.assertEqual(sofa['status'], 'confirmed')
            self.assertEqual(sofa['txHash'], tx_hash)
            self.assertEqual(sofa.type, "TokenPayment")
            self.assertEqual(sofa['contractAddress'], contract.address)
            self.assertEqual(sofa['value'], hex(10 ** token['decimals']))
            self.assertEqual(sofa['toAddress'], TEST_ADDRESS)
            # now test PNs from toshi generated transactions
            raw_tx = await contract.transfer.get_raw_tx.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** token['decimals'])
            tx_hash = await self.send_raw_tx(raw_tx, wait_on_tx_confirmation=False)
            pn = await push_client.get()
            sofa = parse_sofa_message(pn[1]['message'])
            self.assertEqual(sofa['status'], 'confirmed')
            self.assertEqual(sofa['txHash'], tx_hash)
            self.assertEqual(sofa.type, "TokenPayment")
            self.assertEqual(sofa['contractAddress'], contract.address)
            self.assertEqual(sofa['value'], hex(10 ** token['decimals']))
            self.assertEqual(sofa['toAddress'], TEST_ADDRESS)
            await asyncio.sleep(0.1)
            async with self.pool.acquire() as con:
                balance = await con.fetchrow("SELECT * FROM token_balances WHERE eth_address = $1 AND contract_address = $2",
                                             TEST_ADDRESS, contract.address)
            self.assertEqual(int(balance['value'], 16), (10 ** token['decimals']) * (3 if token['symbol'] != token_args[-1][0] else 2),
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
        tx_hash = await contract.transfer.set_sender(TEST_PRIVATE_KEY)(TEST_ADDRESS_2, 20 * 10 ** 18, startgas=61530, wait_for_confirmation=False)

        # process pending transactions
        await monitor.filter_poll()

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

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_weth_deposits_and_withdrawals(self, *, parity, push_client, monitor):
        """Tests the special handling of the WETH contract's deposit and withdrawal methods"""
        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        weth = await Contract.from_source_code(WETH_CONTRACT.encode('utf8'), "WETH9", deployer_private_key=FAUCET_PRIVATE_KEY)
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO tokens (contract_address, symbol, name, decimals) VALUES ($1, $2, $3, $4)",
                              weth.address, "WETH", "Wrapped Ether", 18)

        # monkey patch WETH contract variable
        import toshieth.constants
        toshieth.constants.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.manager
        toshieth.manager.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.monitor
        toshieth.monitor.WETH_CONTRACT_ADDRESS = weth.address

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # make sure tokens are initiated
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)

        # deposit ether into WETH
        tx_hash = await self.send_tx(TEST_PRIVATE_KEY, weth.address, 5 * 10 ** 18, data="0xd0e30db0")
        await self.wait_on_tx_confirmation(tx_hash)

        self.assertEqual(await weth.balanceOf(TEST_ADDRESS), 5 * 10 ** 18)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['symbol'], "WETH")
        self.assertEqual(body['tokens'][0]['value'], hex(5 * 10 ** 18))

        resp = await self.fetch("/balance/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertLess(int(body['confirmed_balance'], 16), 5 * 10 ** 18)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_contract_internal_erc20_transfers(self, *, parity, push_client, monitor):
        """Tests that transfers triggered by another contract (e.g. an exchange) trigger balance updates for the tokens involved"""

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        zrx = await self.deploy_erc20_contract("ZRX", "0x", 18)
        tok = await self.deploy_erc20_contract("TOK", "TOK Token", 18)
        ken = await self.deploy_erc20_contract("KEN", "KEN Token", 18)

        exchange = await Contract.from_source_code(SIMPLE_EXCHANGE_CONTRACT.encode('utf8'), "SimpleExchange", constructor_data=[zrx.address], deployer_private_key=FAUCET_PRIVATE_KEY)

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS_2, 10 * 10 ** 18)

        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)
        await tok.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)
        await ken.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)

        await zrx.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await zrx.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        await tok.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await tok.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        await ken.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await ken.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        await exchange.createOrder.set_sender(TEST_PRIVATE_KEY)(
            ken.address, 5 * 10 ** 18, tok.address, 5 * 10 ** 18)
        await exchange.fillOrder.set_sender(TEST_PRIVATE_KEY_2)(TEST_ADDRESS)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS), 9 * 10 ** 18)
        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS_2), 9 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS), 5 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)
        self.assertEqual(await ken.balanceOf(TEST_ADDRESS), 5 * 10 ** 18)
        self.assertEqual(await ken.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 3)

        has_tok = has_ken = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'KEN':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_ken = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and has_ken)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 3)

        has_tok = has_ken = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'KEN':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_ken = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and has_ken)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_contract_internal_erc20_transfers_with_weth(self, *, parity, push_client, monitor):
        """Tests that transfers triggered by another contract (e.g. an exchange) trigger balance updates for the tokens involved, using WETH"""

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        zrx = await self.deploy_erc20_contract("ZRX", "0x", 18)
        tok = await self.deploy_erc20_contract("TOK", "TOK Token", 18)
        weth = await Contract.from_source_code(WETH_CONTRACT.encode('utf8'), "WETH9", deployer_private_key=FAUCET_PRIVATE_KEY)
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO tokens (contract_address, symbol, name, decimals) VALUES ($1, $2, $3, $4)",
                              weth.address, "WETH", "Wrapped Ether", 18)

        # monkey patch WETH contract variable
        import toshieth.constants
        toshieth.constants.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.manager
        toshieth.manager.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.monitor
        toshieth.monitor.WETH_CONTRACT_ADDRESS = weth.address

        exchange = await Contract.from_source_code(SIMPLE_EXCHANGE_CONTRACT.encode('utf8'), "SimpleExchange", constructor_data=[zrx.address], deployer_private_key=FAUCET_PRIVATE_KEY)

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS_2, 10 * 10 ** 18)

        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)
        await tok.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)
        await weth.deposit.set_sender(TEST_PRIVATE_KEY)(value=5 * 10 ** 18)

        await zrx.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await zrx.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        await tok.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await tok.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        await weth.approve.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await weth.approve.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        await exchange.createOrder.set_sender(TEST_PRIVATE_KEY)(
            weth.address, 5 * 10 ** 18, tok.address, 5 * 10 ** 18)
        await exchange.fillOrder.set_sender(TEST_PRIVATE_KEY_2)(TEST_ADDRESS)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS), 9 * 10 ** 18)
        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS_2), 9 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS), 5 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)
        self.assertEqual(await weth.balanceOf(TEST_ADDRESS), 0)
        self.assertEqual(await weth.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and not has_weth)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 3)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and has_weth)

        resp = await self.fetch("/balance/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        balance_before_withdral = int(body['confirmed_balance'], 16)

        await weth.withdraw.set_sender(TEST_PRIVATE_KEY_2)(5 * 10 ** 18)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and not has_weth)
        resp = await self.fetch("/balance/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)

        balance_after_withdral = int(body['confirmed_balance'], 16)
        self.assertLess(balance_before_withdral, balance_after_withdral)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_contract_internal_erc20_transfers_with_weth_through_toshi(self, *, parity, push_client, monitor):
        """Tests that transfers triggered by another contract (e.g. an exchange) trigger balance updates for the tokens involved, using WETH, sending all calls through toshi http endpoints"""

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        zrx = await self.deploy_erc20_contract("ZRX", "0x", 18)
        tok = await self.deploy_erc20_contract("TOK", "TOK Token", 18)
        weth = await Contract.from_source_code(WETH_CONTRACT.encode('utf8'), "WETH9", deployer_private_key=FAUCET_PRIVATE_KEY)
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO tokens (contract_address, symbol, name, decimals) VALUES ($1, $2, $3, $4)",
                              weth.address, "WETH", "Wrapped Ether", 18)

        # monkey patch WETH contract variable
        import toshieth.constants
        toshieth.constants.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.manager
        toshieth.manager.WETH_CONTRACT_ADDRESS = weth.address
        import toshieth.monitor
        toshieth.monitor.WETH_CONTRACT_ADDRESS = weth.address

        exchange = await Contract.from_source_code(SIMPLE_EXCHANGE_CONTRACT.encode('utf8'), "SimpleExchange", constructor_data=[zrx.address], deployer_private_key=FAUCET_PRIVATE_KEY)

        await self.faucet(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS_2, 10 * 10 ** 18)

        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await zrx.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)
        await tok.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS_2, 10 * 10 ** 18)

        raw_tx = await weth.deposit.get_raw_tx.set_sender(TEST_PRIVATE_KEY)(value=5 * 10 ** 18)
        await self.send_raw_tx(raw_tx)

        raw_tx = await zrx.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)
        raw_tx = await zrx.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)

        raw_tx = await tok.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)
        raw_tx = await tok.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)

        raw_tx = await weth.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)
        raw_tx = await weth.approve.get_raw_tx.set_sender(TEST_PRIVATE_KEY_2)(exchange.address, 0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff)
        await self.send_raw_tx(raw_tx)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)
        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        raw_tx = await exchange.createOrder.get_raw_tx.set_sender(TEST_PRIVATE_KEY)(
            weth.address, 5 * 10 ** 18, tok.address, 5 * 10 ** 18)
        await self.send_raw_tx(raw_tx)
        raw_tx = await exchange.fillOrder.get_raw_tx.set_sender(TEST_PRIVATE_KEY_2)(TEST_ADDRESS)
        await self.send_raw_tx(raw_tx)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS), 9 * 10 ** 18)
        self.assertEqual(await zrx.balanceOf(TEST_ADDRESS_2), 9 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS), 5 * 10 ** 18)
        self.assertEqual(await tok.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)
        self.assertEqual(await weth.balanceOf(TEST_ADDRESS), 0)
        self.assertEqual(await weth.balanceOf(TEST_ADDRESS_2), 5 * 10 ** 18)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and not has_weth)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 3)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and has_weth)

        resp = await self.fetch("/balance/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        balance_before_withdral = int(body['confirmed_balance'], 16)

        raw_tx = await weth.withdraw.get_raw_tx.set_sender(TEST_PRIVATE_KEY_2)(5 * 10 ** 18)
        await self.send_raw_tx(raw_tx)

        await monitor.filter_poll()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS_2))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2, body)

        has_tok = has_weth = has_zrx = False
        for token in body['tokens']:
            if token['symbol'] == 'TOK':
                self.assertEqual(token['value'], hex(5 * 10 ** 18))
                has_tok = True
            elif token['symbol'] == 'WETH':
                has_weth = True
            elif token['symbol'] == 'ZRX':
                self.assertEqual(token['value'], hex(9 * 10 ** 18))
                has_zrx = True
            else:
                self.fail("unexpected token symbol")

        self.assertTrue(has_tok and has_zrx and not has_weth)
        resp = await self.fetch("/balance/{}".format(TEST_ADDRESS_2))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)

        balance_after_withdral = int(body['confirmed_balance'], 16)
        self.assertLess(balance_before_withdral, balance_after_withdral)

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True)
    async def test_websockets_dont_see_token_pns(self, *, parity, push_client, monitor):
        """Many bots are broken by token txs right now as the sofa-js library throws an
        error on invalid sofa types. Making sure bots aren't effected by this change
        until we've had time to update the libraries and important bots"""

        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']

        contract = await self.deploy_erc20_contract("TST", "Test Token", 18)
        await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 * 10 ** 18)
        await self.faucet(TEST_ADDRESS, 10 ** 18)

        ws_con = await self.websocket_connect(TEST_PRIVATE_KEY_2)
        await ws_con.call("subscribe", [TEST_ADDRESS_2])

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY_2, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        await self.send_tx(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 5 * 10 ** 18, token_address=contract.address)

        pn = await push_client.get()
        self.assertIsNotNone(pn)

        result = await ws_con.read(timeout=1)
        self.assertIsNone(result)
