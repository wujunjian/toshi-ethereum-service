from tornado.escape import json_decode
from tornado.testing import gen_test

from tokeneth.app import urls
from asyncbb.test.base import AsyncHandlerTest
from asyncbb.test.database import requires_database
from tokenbrowser.crypto import sign_payload
from tokenbrowser.utils import data_decoder
from asyncbb.ethereum.test.parity import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS

TEST_PRIVATE_KEY = data_decoder("0xe8f32e723decf4051aefac8e2c93c9c5b214313817cdb01a1494b917c8436b35")
TEST_ADDRESS = "0x056db290f8ba3250ca64a45d16284d04bc6f5fbf"

TEST_ADDRESS_2 = "0x056db290f8ba3250ca64a45d16284d04bc000000"

TEST_APN_ID = "64be4fe95ba967bb533f0c240325942b9e1f881b5cd2982568a305dd4933e0bd"
TEST_GCM_ID = "64be4fe95ba967bb533f0c240325942b9e1f881b5cd2982568a305dd4933e0bd"

TEST_APN_ID_2 = "a952655fb6688289ea7f81f9b21667e2a156cf651dcabf69c7878abfc4cb7bd0"
TEST_GCM_ID_2 = "a952655fb6688289ea7f81f9b21667e2a156cf651dcabf69c7878abfc4cb7bd0"

class APNRegistrationTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def fetch(self, url, **kwargs):
        return super(APNRegistrationTest, self).fetch("/v1{}".format(url), **kwargs)

    @gen_test
    @requires_database
    async def test_register_for_push_notifications(self):

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/apn/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_pn_registration(self):

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(data_decoder(FAUCET_PRIVATE_KEY), body['payload'])

        resp = await self.fetch("/apn/register", method="POST", body=body)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 0)

    @gen_test
    @requires_database
    async def test_reregister_for_push_notifications(self):

        """tests that registering an address that is already registered
        simply ignores the new registration attempt"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO apn_registrations VALUES ($1, $2)",
                               TEST_APN_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/apn/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_replace_previous_push_notification_registration(self):

        """tests that registering an existing registration_id with a new
        token_id replaces the old token id"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO apn_registrations VALUES ($1, $2), ($3, $4)",
                               TEST_APN_ID, FAUCET_ADDRESS, TEST_APN_ID_2, TEST_ADDRESS_2)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/apn/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)


    @gen_test
    @requires_database
    async def test_deregister_notifications(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO apn_registrations VALUES ($1, $2)",
                               TEST_APN_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/apn/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_deregister_notifications_when_not_registered(self):

        """Makes sure that there is no failure when deregistering when the
        registration id hasn't been registered"""

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/apn/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_deregistration(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO apn_registrations VALUES ($1, $2)",
                               TEST_APN_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_APN_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(data_decoder(FAUCET_PRIVATE_KEY), body['payload'])

        resp = await self.fetch("/apn/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM apn_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)


class GCMRegistrationTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def fetch(self, url, **kwargs):
        return super(GCMRegistrationTest, self).fetch("/v1{}".format(url), **kwargs)

    @gen_test
    @requires_database
    async def test_register_for_push_notifications(self):

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/gcm/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_pn_registration(self):

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(data_decoder(FAUCET_PRIVATE_KEY), body['payload'])

        resp = await self.fetch("/gcm/register", method="POST", body=body)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 0)

    @gen_test
    @requires_database
    async def test_reregister_for_push_notifications(self):

        """tests that registering an address that is already registered
        simply ignores the new registration attempt"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO gcm_registrations VALUES ($1, $2)",
                               TEST_GCM_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/gcm/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_replace_previous_push_notification_registration(self):

        """tests that registering an existing registration_id with a new
        token_id replaces the old token id"""

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO gcm_registrations VALUES ($1, $2), ($3, $4)",
                               TEST_GCM_ID, FAUCET_ADDRESS, TEST_GCM_ID_2, TEST_ADDRESS_2)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/gcm/register", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

    @gen_test
    @requires_database
    async def test_deregister_notifications(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO gcm_registrations VALUES ($1, $2)",
                               TEST_GCM_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/gcm/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_deregister_notifications_when_not_registered(self):

        """Makes sure that there is no failure when deregistering when the
        registration id hasn't been registered"""

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(TEST_PRIVATE_KEY, body['payload'])

        resp = await self.fetch("/gcm/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 204, resp.body)

        async with self.pool.acquire() as con:

            row = await con.fetchrow("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNone(row)

    @gen_test
    @requires_database
    async def test_invalid_signature_in_deregistration(self):

        async with self.pool.acquire() as con:

            await con.fetchrow("INSERT INTO gcm_registrations VALUES ($1, $2)",
                               TEST_GCM_ID, TEST_ADDRESS)

        resp = await self.fetch("/timestamp")
        self.assertEqual(resp.code, 200)
        timestamp = json_decode(resp.body)['timestamp']

        body = {
            "payload": {
                "registration_id": TEST_GCM_ID,
                "timestamp": timestamp
            },
            "address": TEST_ADDRESS
        }

        body['signature'] = sign_payload(data_decoder(FAUCET_PRIVATE_KEY), body['payload'])

        resp = await self.fetch("/gcm/deregister", method="POST", body=body)

        self.assertEqual(resp.code, 400, resp.body)

        async with self.pool.acquire() as con:

            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ADDRESS)

        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

class PNRegistrationURLSanityCheckTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    @gen_test
    async def test_404_on_invalid_pn_service_url(self):

        resp = await self.fetch("/v1/apn/register")
        self.assertEqual(resp.code, 405)

        resp = await self.fetch("/v1/apn/deregister")
        self.assertEqual(resp.code, 405)

        resp = await self.fetch("/v1/gcm/register")
        self.assertEqual(resp.code, 405)

        resp = await self.fetch("/v1/gcm/deregister")
        self.assertEqual(resp.code, 405)

        resp = await self.fetch("/v1/xxx/register")
        self.assertEqual(resp.code, 404)

        resp = await self.fetch("/v1/xxx/deregister")
        self.assertEqual(resp.code, 404)
