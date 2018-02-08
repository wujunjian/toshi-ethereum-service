# -*- coding: utf-8 -*-
import asyncio
import os
import blockies
import hashlib

from tornado.escape import json_decode
from tornado.testing import gen_test

from toshieth.app import urls
from toshi.test.database import requires_database
from toshi.test.base import AsyncHandlerTest

from toshi.test.base import ToshiWebSocketJsonRPCClient
from toshieth.test.base import EthServiceBaseTest, requires_full_stack
from toshi.test.ethereum.faucet import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from toshi.ethereum.tx import sign_transaction, data_encoder, data_decoder
from toshi.jsonrpc.client import JsonRPCClient
from toshi.sofa import parse_sofa_message
from ethereum.utils import sha3

from toshi.ethereum.contract import Contract

ABC_TOKEN_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"
YAC_TOKEN_ADDRESS = "0x9ab6c6111577c51da46e2c4c93a3622671578657"

ERC20_CONTRACT = """
pragma solidity ^0.4.8;

contract Token {

    uint256 constant MAX_UINT256 = 2**256 - 1;

    mapping (address => uint256) balances;
    mapping (address => mapping (address => uint256)) allowed;

    uint256 public totalSupply;

    string public name;
    uint8 public decimals;
    string public symbol;

    function Token(uint256 _initialAmount,
                   string _tokenName,
                   uint8 _decimalUnits,
                   string _tokenSymbol) {
        balances[msg.sender] = _initialAmount;
        totalSupply = _initialAmount;
        name = _tokenName;
        decimals = _decimalUnits;
        symbol = _tokenSymbol;
    }

    function transfer(address _to, uint256 _value) returns (bool success) {
        require(balances[msg.sender] >= _value);
        balances[msg.sender] -= _value;
        balances[_to] += _value;
        Transfer(msg.sender, _to, _value);
        return true;
    }

    function transferFrom(address _from, address _to, uint256 _value) returns (bool success) {
        uint256 allowance = allowed[_from][msg.sender];
        require(balances[_from] >= _value && allowance >= _value);
        balances[_to] += _value;
        balances[_from] -= _value;
        if (allowance < MAX_UINT256) {
            allowed[_from][msg.sender] -= _value;
        }
        Transfer(_from, _to, _value);
        return true;
    }

    function balanceOf(address _owner) constant returns (uint256 balance) {
        return balances[_owner];
    }

    function approve(address _spender, uint256 _value) returns (bool success) {
        allowed[msg.sender][_spender] = _value;
        Approval(msg.sender, _spender, _value);
        return true;
    }

    function allowance(address _owner, address _spender) constant returns (uint256 remaining) {
      return allowed[_owner][_spender];
    }

    event Transfer(address indexed _from, address indexed _to, uint256 _value);
    event Approval(address indexed _owner, address indexed _spender, uint256 _value);
}
"""

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"
TEST_ADDRESS_2 = "0x819671356713b9e379e8beec9425f15cf8299eca"

TEST_APN_ID = "64be4fe95ba967bb533f0c240325942b9e1f881b5cd2982568a305dd4933e0bd"

class ERC20Test(EthServiceBaseTest):

    async def deploy_erc20_contract(self, symbol, name, decimals):
        sourcecode = ERC20_CONTRACT.encode('utf-8')
        contract_name = "Token"
        constructor_data = [2**256 - 1, name, decimals, symbol]
        contract = await Contract.from_source_code(sourcecode, contract_name, constructor_data=constructor_data, deployer_private_key=FAUCET_PRIVATE_KEY)

        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO tokens (address, symbol, name, decimals) VALUES ($1, $2, $3, $4)",
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
                sofa = json_decode(pn[1]['message'])
                if sofa['status'] == 'confirmed':
                    break
            self.assertEqual(sofa['symbol'], token['symbol'])
            self.assertEqual(sofa['value'], hex(10 ** token['decimals']))
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

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        await asyncio.sleep(0.1)

        await self.send_tx(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 18)

        # wait for unconfirmed and confirmed PN, otherwise the contract send will overwrite it (TODO)
        await push_client.get()
        await push_client.get()

        # test sending new tokens via skel
        await self.send_tx(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 5 * 10 ** 18, token_address=contract.address)

        # wait for unconfirmed and confirmed PN, otherwise the contract send will overwrite it (TODO)
        await push_client.get()
        await push_client.get()

        resp = await self.fetch("/tokens/{}".format(TEST_ADDRESS))

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 1)
        self.assertEqual(body['tokens'][0]['value'], hex(5 * 10 ** 18))

        # test sending tokens when balance isn't updated fails
        await self.get_tx_skel(TEST_PRIVATE_KEY, TEST_ADDRESS_2, 10 * 10 ** 18,
                               token_address=contract.address, expected_response_code=400)
