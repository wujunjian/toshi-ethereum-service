import asyncio
import os

from tornado.testing import gen_test
from tornado.escape import json_decode

from toshi.test.base import ToshiWebSocketJsonRPCClient
from toshieth.test.base import EthServiceBaseTest, requires_full_stack
from toshi.test.ethereum.faucet import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from toshi.ethereum.tx import sign_transaction, data_encoder, data_decoder
from toshi.jsonrpc.client import JsonRPCClient
from toshi.sofa import parse_sofa_message
from ethereum.utils import sha3

from toshi.ethereum.contract import Contract

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
        tokens = {
            "TST": ["Test Token", 18],
            "BOB": ["Big Old Bucks", 10],
            "HMM": ["Hmmmmmm", 5],
            "NOT": ["Not This One", 20]
        }
        contracts = {}

        for symbol in tokens:
            contract = await self.deploy_erc20_contract(symbol, *tokens[symbol])
            contracts[symbol] = contract

        for symbol in tokens:
            if symbol == "NOT":
                continue
            # give "1" of each token (except NOT)
            contract = contracts[symbol]
            await contract.transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** tokens[symbol][1])

            result = await contract.balanceOf(TEST_ADDRESS)
            self.assertEquals(result, 10 ** tokens[symbol][1])

        # force block check to clear out txs pre registration
        await monitor.block_check()

        resp = await self.fetch_signed("/apn/register", signing_key=TEST_PRIVATE_KEY, method="POST", body={
            "registration_id": TEST_APN_ID
        })
        self.assertEqual(resp.code, 204)

        # wait for task to process user's tokens
        await asyncio.sleep(0.1)

        async with self.pool.acquire() as con:
            balances = await con.fetch("SELECT * FROM erc20_balances WHERE address = $1",
                                       TEST_ADDRESS)

        self.assertEqual(len(balances), len(tokens) - 1)
        for balance in balances:
            self.assertTrue(balance['symbol'] in tokens)
            token = tokens[balance['symbol']]
            self.assertEqual(int(balance['value'], 16), 10 ** token[1])

        await self.send_tx(FAUCET_PRIVATE_KEY, TEST_ADDRESS, 10 ** 18)

        pn = await push_client.get()
        sofa = parse_sofa_message(pn[1]['message'])
        self.assertEqual(sofa['currency'], "ETH")

        # wait for confirmed message as well, otherwise the contract send will overwrite it (TODO)
        await push_client.get()

        # test that receiving new tokens triggers a PN
        for symbol in tokens:
            token = tokens[symbol]
            await contracts[symbol].transfer.set_sender(FAUCET_PRIVATE_KEY)(TEST_ADDRESS, 10 ** token[1], wait_for_confirmation=False)
            while True:
                pn = await push_client.get()
                sofa = parse_sofa_message(pn[1]['message'])
                if sofa['status'] == 'confirmed':
                    break
            self.assertEqual(sofa['currency'], symbol)
            self.assertEqual(sofa['value'], hex(10 ** token[1]))
            await asyncio.sleep(0.1)
            async with self.pool.acquire() as con:
                balance = await con.fetchrow("SELECT * FROM erc20_balances WHERE address = $1 AND symbol = $2",
                                             TEST_ADDRESS, symbol)
            self.assertEqual(int(balance['value'], 16), (10 ** token[1]) * (2 if symbol != "NOT" else 1),
                             "invalid balance after updating {} token".format(symbol))
