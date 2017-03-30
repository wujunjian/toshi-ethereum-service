from tokenservices.test.base import AsyncHandlerTest
from tokeneth.app import urls
from tornado.escape import json_decode
from tornado.testing import gen_test
from tokenservices.test.database import requires_database
from tokenservices.test.ethereum.parity import requires_parity
from tokenservices.test.ethereum.faucet import FaucetMixin, FAUCET_ADDRESS
from tokenservices.utils import parse_int

class BalanceTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    @gen_test(timeout=30)
    @requires_database
    @requires_parity
    async def test_get_balance(self):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(addr, val)

        resp = await self.fetch('/balance/{}'.format(addr))

        self.assertEqual(resp.code, 200)

        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), val)
        self.assertEqual(parse_int(data['unconfirmed_balance']), val)

    @gen_test(timeout=30)
    @requires_database
    @requires_parity
    async def test_get_balance_of_empty_address(self):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'

        resp = await self.fetch('/balance/{}'.format(addr))

        self.assertEqual(resp.code, 200)

        data = json_decode(resp.body)
        self.assertEqual(data['confirmed_balance'], "0x0")
        self.assertEqual(data['unconfirmed_balance'], "0x0")

    @gen_test(timeout=30)
    @requires_database
    @requires_parity
    async def test_get_balance_with_unconfirmed_txs(self):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(addr, val)

        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO transactions VALUES ('0x2f321aa116146a9bc62b61c76508295f708f42d56340c9e613ebfc27e33f240c', $1, $2, 0, $3)",
                              FAUCET_ADDRESS, addr, val)

        resp = await self.fetch('/balance/{}'.format(addr))

        self.assertEqual(resp.code, 200)

        data = json_decode(resp.body)
        self.assertEqual(parse_int(data['confirmed_balance']), val)
        self.assertEqual(parse_int(data['unconfirmed_balance']), val * 2)
