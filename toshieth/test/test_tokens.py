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
                "(contract_address, symbol, name, decimals, icon, hash, format) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                "0x2222222222222222222222222222222222222222", "YAC", "Yet Another Currency Token", 2, image, hash, 'png'
            )

        resp = await self.fetch(
            "/tokens", method="GET"
        )

        self.assertResponseCodeEqual(resp, 200)
        body = json_decode(resp.body)
        self.assertEqual(len(body['tokens']), 2)

        for token in body['tokens']:
            icon_url = token['icon']
            resp = await self.fetch(
                icon_url, method="GET"
            )
            self.assertResponseCodeEqual(resp, 200)
            self.assertEqual(resp.headers.get('Content-Type'),
                             'image/png')
            self.assertEqual(resp.body, image)

        resp = await self.fetch("/token/0x0000000000000000000000000000000000000000.png")
        self.assertResponseCodeEqual(resp, 404)
