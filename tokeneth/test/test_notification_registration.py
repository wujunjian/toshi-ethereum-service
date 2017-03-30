import time

from tornado.escape import json_encode, json_decode
from tornado.testing import gen_test

from tokeneth.app import urls
from tokenservices.test.base import AsyncHandlerTest
from tokenservices.test.database import requires_database
from tokenservices.request import sign_request
from tokenservices.test.ethereum.parity import FAUCET_PRIVATE_KEY

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)
class NotificationRegistrationTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    @gen_test
    @requires_database
    async def test_register_for_notifications(self):

        body = {
            "addresses": [TEST_ID_ADDRESS, TEST_WALLET_ADDRESS]
        }

        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_registration(self):

        body = {
            "addresses": [TEST_ID_ADDRESS, TEST_WALLET_ADDRESS]
        }

        timestamp = int(time.time())
        signature = sign_request(FAUCET_PRIVATE_KEY, "POST", "/v1/register", timestamp, json_encode(body).encode('utf-8'))

        resp = await self.fetch_signed("/register", method="POST", body=body,
                                       address=TEST_ID_ADDRESS, signature=signature, timestamp=timestamp)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 0)

    @gen_test
    @requires_database
    async def test_reregister_for_notifications(self):

        """tests that registering an address that is already registered
        simply ignores the new registration attempt"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2), ($1, $3)",
                               TEST_ID_ADDRESS, TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)

        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }

        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)

    @gen_test
    @requires_database
    async def test_deregister_notifications(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2)",
                               TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)

        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }

        resp = await self.fetch_signed("/deregister", signing_key=TEST_ID_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_deregister_notifications_when_not_registered(self):

        """Makes sure that there is no failure when deregistering an address
        that hasn't been registered"""

        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }

        resp = await self.fetch_signed("/deregister", signing_key=TEST_ID_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_deregistration(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2)",
                               TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)

        body = {
            "addresses": [TEST_ID_ADDRESS, TEST_WALLET_ADDRESS]
        }

        timestamp = int(time.time())
        signature = sign_request(FAUCET_PRIVATE_KEY, "POST", "/v1/deregister", timestamp, json_encode(body).encode('utf-8'))

        resp = await self.fetch_signed("/deregister", method="POST", body=body,
                                       address=TEST_ID_ADDRESS, signature=signature, timestamp=timestamp)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_list_subscriptions(self):

        resp = await self.fetch_signed("/subscriptions", signing_key=TEST_ID_KEY)
        subs = json_decode(resp.body)['subscriptions']
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 0)

        await self.fetch_signed("/register", body={"addresses": [TEST_ID_ADDRESS]},
                                method="POST", signing_key=TEST_ID_KEY)
        resp = await self.fetch_signed("/subscriptions", signing_key=TEST_ID_KEY)
        subs = json_decode(resp.body)['subscriptions']
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0], TEST_ID_ADDRESS)

        await self.fetch_signed("/register", body={"addresses": [TEST_ID_ADDRESS, TEST_WALLET_ADDRESS]},
                                method="POST", signing_key=TEST_ID_KEY)
        resp = await self.fetch_signed("/subscriptions", signing_key=TEST_ID_KEY)
        subs = json_decode(resp.body)['subscriptions']
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 2)
        self.assertEqual(set(subs), {TEST_ID_ADDRESS, TEST_WALLET_ADDRESS})

        await self.fetch_signed("/deregister", body={"addresses": [TEST_ID_ADDRESS]},
                                method="POST", signing_key=TEST_ID_KEY)
        resp = await self.fetch_signed("/subscriptions", signing_key=TEST_ID_KEY)
        subs = json_decode(resp.body)['subscriptions']
        self.assertIsNotNone(subs)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0], TEST_WALLET_ADDRESS)
