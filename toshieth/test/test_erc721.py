# -*- coding: utf-8 -*-
import asyncio
import os

from tornado.escape import json_decode
from tornado.testing import gen_test

from toshieth.test.base import EthServiceBaseTest, requires_full_stack
from toshi.test.ethereum.faucet import FAUCET_PRIVATE_KEY
from toshi.ethereum.utils import private_key_to_address, data_decoder

from toshi.ethereum.contract import Contract

ABC_TOKEN_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"
YAC_TOKEN_ADDRESS = "0x9ab6c6111577c51da46e2c4c93a3622671578657"

ERC721_CONTRACT = open(os.path.join(os.path.dirname(__file__), "erc721.sol")).read()
TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_PRIVATE_KEY_2 = data_decoder("0x8945608e66736aceb34a83f94689b4e98af497ffc9dc2004a93824096330fa77")
TEST_ADDRESS = private_key_to_address(TEST_PRIVATE_KEY)
TEST_ADDRESS_2 = private_key_to_address(TEST_PRIVATE_KEY_2)

TEST_APN_ID = "64be4fe95ba967bb533f0c240325942b9e1f881b5cd2982568a305dd4933e0bd"

class ERC721Test(EthServiceBaseTest):

    async def deploy_erc721_contract(self, symbol, name):
        sourcecode = ERC721_CONTRACT.encode('utf-8')
        contract_name = "NonFungibleToken"
        constructor_data = [name, symbol]
        contract = await Contract.from_source_code(sourcecode, contract_name, constructor_data=constructor_data, deployer_private_key=FAUCET_PRIVATE_KEY)
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO collectibles (contract_address, name) VALUES ($1, $2)",
                              contract.address, name)

        return contract

    @gen_test(timeout=60)
    @requires_full_stack(parity=True, push_client=True, block_monitor=True, collectible_monitor=True)
    async def test_erc721_transfer(self, *, parity, push_client, monitor, collectible_monitor):
        os.environ['ETHEREUM_NODE_URL'] = parity.dsn()['url']
        constructor_args = [
            ["TST", "Test ERC721 Token"]
        ]
        collectibles = {}
        contracts = {}

        for args in constructor_args:
            print("deploying {}".format(args[1]))
            contract = await self.deploy_erc721_contract(*args)
            contracts[contract.address] = contract
            collectibles[contract.address] = {"symbol": args[0], "name": args[1], "contract": contract, 'tokens': {}}
            args.append(contract.address)

        # "mint" some tokens
        for collectible in collectibles.values():
            contract = collectible['contract']
            for i in range(10):
                token_id = int(os.urandom(16).hex(), 16)
                await contract.mint.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, token_id, "")
                collectible['tokens'][hex(token_id)] = TEST_ADDRESS

            result = await contract.balanceOf(TEST_ADDRESS)
            self.assertEquals(result, len(collectible['tokens']))

        # force block check to clear out txs pre registration
        await monitor.block_check()
        await asyncio.sleep(0.1)

        collectible = next(iter(collectibles.values()))
        contract = collectible['contract']
        users_tokens = [token_id for token_id, owner in collectible['tokens'].items() if owner == TEST_ADDRESS]

        resp = await self.fetch("/collectibles/{}/{}".format(TEST_ADDRESS, contract.address))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), len(users_tokens))

        for token in body['tokens']:
            self.assertIn(token['token_id'], users_tokens)

        # give TEST_ADDRESS some funds
        await self.wait_on_tx_confirmation(await self.send_tx(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 18))

        token_id = next(iter(collectible['tokens'].keys()))
        await contract.transfer.set_sender(TEST_PRIVATE_KEY)(TEST_ADDRESS_2, token_id)

        # force block check to clear out txs
        await monitor.block_check()
        await asyncio.sleep(0.1)

        resp = await self.fetch("/collectibles/{}/{}".format(TEST_ADDRESS_2, contract.address))
        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['token_id'], token_id)

        # make sure the token has been removed from the original owner
        resp = await self.fetch("/collectibles/{}/{}".format(TEST_ADDRESS, contract.address))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), len(users_tokens) - 1)
