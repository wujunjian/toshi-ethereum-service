# -*- coding: utf-8 -*-
import blockies
import hashlib

from tornado.escape import json_decode
from tornado.testing import gen_test

from toshieth.app import urls
from toshi.test.database import requires_database
from toshi.test.base import AsyncHandlerTest

# reuse constant from test_avatar.py (toshiid)
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"


class TokenHandlerTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/token/"):
            path = "/v1{}".format(path)
        return super().get_url(path)

    @gen_test
    @requires_database
    async def test_tokens(self):
        image = blockies.create(TEST_ADDRESS, size=8, scale=12)
        hasher = hashlib.md5()
        hasher.update(image)
        hash = hasher.hexdigest()

        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO tokens "
                "(contract_address, symbol, name, decimals, icon, hash, format) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                "0x1111111111111111111111111111111111111111", "ABC", "Awesome Balls Currency Token", 18, image, hash, 'png'
            )
            await con.execute(
                "INSERT INTO tokens "
                "(contract_address, symbol, name, decimals) "
                "VALUES ($1, $2, $3, $4)",
                "0x2222222222222222222222222222222222222222", "YAC", "Yet Another Currency Token", 2
            )

        resp = await self.fetch(
            "/tokens", method="GET"
        )

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        for token in body['tokens']:
            icon_url = token['icon']
            if token['symbol'] == "YAC":
                self.assertIsNone(token['icon'])
            else:
                self.assertEqual(self.get_url("/token/{}.png".format(token['contract_address'])),
                                 icon_url)
                resp = await self.fetch(
                    icon_url, method="GET"
                )
                self.assertResponseCodeEqual(resp, 200)
                self.assertEqual(resp.headers.get('Content-Type'),
                                 'image/png')
                self.assertEqual(resp.body, image)

        resp = await self.fetch("/token/0x0000000000000000000000000000000000000000.png")
        self.assertResponseCodeEqual(resp, 404)

    @gen_test
    @requires_database
    async def test_token_balances(self):
        image = blockies.create(TEST_ADDRESS, size=8, scale=12)
        hasher = hashlib.md5()
        hasher.update(image)
        hash = hasher.hexdigest()

        async with self.pool.acquire() as con:
            await con.execute(
                "INSERT INTO tokens "
                "(contract_address, symbol, name, decimals, icon, hash, format) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                "0x1111111111111111111111111111111111111111", "ABC", "Awesome Balls Currency Token", 18, image, hash, 'png'
            )
            await con.execute(
                "INSERT INTO tokens "
                "(contract_address, symbol, name, decimals) "
                "VALUES ($1, $2, $3, $4)",
                "0x2222222222222222222222222222222222222222", "YAC", "Yet Another Currency Token", 2
            )
            await con.executemany(
                "INSERT INTO token_balances "
                "(contract_address, eth_address, value) "
                "VALUES ($1, $2, $3)",
                [("0x1111111111111111111111111111111111111111", TEST_ADDRESS, hex(10 ** 18)),
                 ("0x2222222222222222222222222222222222222222", TEST_ADDRESS, hex(10 ** 18))])
            await con.executemany(
                "INSERT INTO token_registrations "
                "(eth_address) VALUES ($1)",
                [(TEST_ADDRESS,)])

        resp = await self.fetch(
            "/tokens/{}".format(TEST_ADDRESS), method="GET"
        )

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        for token in body['tokens']:
            self.assertEqual(token['value'], hex(10 ** 18))
            icon_url = token['icon']
            if token['symbol'] == "YAC":
                self.assertIsNone(icon_url)
            else:
                self.assertEqual(self.get_url("/token/{}.png".format(token['contract_address'])),
                                 icon_url)
                resp = await self.fetch(
                    icon_url, method="GET"
                )
                self.assertResponseCodeEqual(resp, 200)
                self.assertEqual(resp.headers.get('Content-Type'),
                                 'image/png')
                self.assertEqual(resp.body, image)
