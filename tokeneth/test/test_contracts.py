import asyncio
from tornado.testing import gen_test
from tornado.escape import json_decode

from tokeneth.test.base import EthServiceBaseTest, requires_full_stack
from tokenservices.test.ethereum.faucet import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokenservices.ethereum.tx import sign_transaction
from tokenservices.jsonrpc.client import JsonRPCClient

class ContractTest(EthServiceBaseTest):

    @gen_test(timeout=60)
    @requires_full_stack(parity=True)
    async def test_raw_deploy_contract(self, *, parity):
        """Tests that sending a raw transaction with a contract deployment works"""

        # contract data
        data = "0x6060604052341561000c57fe5b6040516102b83803806102b8833981016040528080518201919050505b806000908051906020019061003f929190610047565b505b506100ec565b828054600181600116156101000203166002900490600052602060002090601f016020900481019282601f1061008857805160ff19168380011785556100b6565b828001600101855582156100b6579182015b828111156100b557825182559160200191906001019061009a565b5b5090506100c391906100c7565b5090565b6100e991905b808211156100e55760008160009055506001016100cd565b5090565b90565b6101bd806100fb6000396000f30060606040526000357c0100000000000000000000000000000000000000000000000000000000900463ffffffff168063cfae32171461003b575bfe5b341561004357fe5b61004b6100d4565b604051808060200182810382528381815181526020019150805190602001908083836000831461009a575b80518252602083111561009a57602082019150602081019050602083039250610076565b505050905090810190601f1680156100c65780820380516001836020036101000a031916815260200191505b509250505060405180910390f35b6100dc61017d565b60008054600181600116156101000203166002900480601f0160208091040260200160405190810160405280929190818152602001828054600181600116156101000203166002900480156101725780601f1061014757610100808354040283529160200191610172565b820191906000526020600020905b81548152906001019060200180831161015557829003601f168201915b505050505090505b90565b6020604051908101604052806000815250905600a165627a7a72305820493059270656b40625319934bd6e91b0e68cf32c54c099dfc6cf540e40c91b9500290000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000000c68656c6c6f20776f726c64210000000000000000000000000000000000000000"

        resp = await self.fetch("/tx/skel", method="POST", body={
            "from": FAUCET_ADDRESS,
            "data": data
        })
        self.assertEqual(resp.code, 200)
        body = json_decode(resp.body)
        tx = sign_transaction(body['tx'], FAUCET_PRIVATE_KEY)
        resp = await self.fetch("/tx", method="POST", body={
            "tx": tx
        })
        self.assertEqual(resp.code, 200, resp.body)

        await self.wait_on_tx_confirmation(json_decode(resp.body)['tx_hash'])

        # test that contract txs from outside are handled correctly
        resp = await self.fetch_signed("/apn/register", signing_key=FAUCET_PRIVATE_KEY, method="POST", body={
            "registration_id": "blahblahblah"
        })

        resp = await self.fetch("/tx/skel", method="POST", body={
            "from": FAUCET_ADDRESS,
            "data": data
        })
        self.assertEqual(resp.code, 200)
        body = json_decode(resp.body)
        tx = sign_transaction(body['tx'], FAUCET_PRIVATE_KEY)

        # deploy manually
        rpcclient = JsonRPCClient(parity.dsn()['url'])
        tx_hash = await rpcclient.eth_sendRawTransaction(tx)

        await self.wait_on_tx_confirmation(tx_hash)
        await asyncio.sleep(5) # make sure the monitor has had a chance to process this

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM transactions WHERE hash = $1", tx_hash)
        self.assertEqual(len(rows), 1)
