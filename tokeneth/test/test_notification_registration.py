import time

from tornado.escape import json_encode
from tornado.testing import gen_test

from tokeneth.app import urls
from tokenservices.test.base import AsyncHandlerTest
from asyncbb.test.database import requires_database
from tokenbrowser.request import sign_request
from tokenbrowser.utils import data_decoder
from asyncbb.ethereum.test.parity import FAUCET_PRIVATE_KEY

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

TEST_ADDRESS_2 = "0x056db290f8ba3250ca64a45d16284d04bc000000"

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
            "addresses": [TEST_ADDRESS, TEST_ADDRESS_2]
        }

        resp = await self.fetch_signed("/register", signing_key=TEST_PRIVATE_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_registration(self):

        body = {
            "addresses": [TEST_ADDRESS, TEST_ADDRESS_2]
        }

        timestamp = int(time.time())
        signature = sign_request(FAUCET_PRIVATE_KEY, "POST", "/v1/register", timestamp, json_encode(body).encode('utf-8'))

        resp = await self.fetch_signed("/register", method="POST", body=body,
                                       address=TEST_ADDRESS, signature=signature, timestamp=timestamp)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 0)

    @gen_test
    @requires_database
    async def test_reregister_for_notifications(self):

        """tests that registering an address that is already registered
        simply ignores the new registration attempt"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2), ($1, $3)",
                               TEST_ADDRESS, TEST_ADDRESS, TEST_ADDRESS_2)

        body = {
            "addresses": [TEST_ADDRESS_2]
        }

        resp = await self.fetch_signed("/register", signing_key=TEST_PRIVATE_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)

    @gen_test
    @requires_database
    async def test_deregister_notifications(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2)",
                               TEST_ADDRESS, TEST_ADDRESS_2)

        body = {
            "addresses": [TEST_ADDRESS_2]
        }

        resp = await self.fetch_signed("/deregister", signing_key=TEST_PRIVATE_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_deregister_notifications_when_not_registered(self):

        """Makes sure that there is no failure when deregistering an address
        that hasn't been registered"""

        body = {
            "addresses": [TEST_ADDRESS_2]
        }

        resp = await self.fetch_signed("/deregister", signing_key=TEST_PRIVATE_KEY, method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_deregistration(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO notification_registrations VALUES ($1, $2)",
                               TEST_ADDRESS, TEST_ADDRESS_2)

        body = {
            "addresses": [TEST_ADDRESS, TEST_ADDRESS_2]
        }

        timestamp = int(time.time())
        signature = sign_request(FAUCET_PRIVATE_KEY, "POST", "/v1/deregister", timestamp, json_encode(body).encode('utf-8'))

        resp = await self.fetch_signed("/deregister", method="POST", body=body,
                                       address=TEST_ADDRESS, signature=signature, timestamp=timestamp)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM notification_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)
