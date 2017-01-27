import unittest
import asyncio
import tokeneth.monitor
import warnings

from tokenservices.test.base import AsyncHandlerTest
from tokeneth.app import urls
from tornado.escape import json_decode
from tornado.testing import gen_test
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.test.parity import requires_parity
from asyncbb.ethereum.test.faucet import FaucetMixin
from tokenbrowser.tx import sign_transaction
from tokenbrowser.sofa import SofaPayment, parse_sofa_message

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)
from asyncbb.ethereum.test.parity import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokeneth.test.test_pn_registration import TEST_GCM_ID, TEST_GCM_ID_2

class SimpleBlockMonitor(tokeneth.monitor.BlockMonitor):

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.confirmation_queue = asyncio.Queue()

    async def send_transaction_notifications(self, transaction):
        self.confirmation_queue.put_nowait(transaction)

class MockPushClient:

    def __init__(self):
        self.send_queue = asyncio.Queue()

    async def send(self, token_id, device_token, data):

        if len(data) > 1 or 'message' not in data:
            raise NotImplementedError("Only data key allowed is 'message'")

        self.send_queue.put_nowait((device_token, data))

class SimpleMonitorTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, url):
        return "/v1{}".format(url)

    @unittest.skip("TODO: figure out issues with this randomly failing")
    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_get_block_confirmation(self):

        monitor = SimpleBlockMonitor(self._app.connection_pool, self._app.config['ethereum']['url'])
        await asyncio.sleep(0.1)
        self._app.monitor = monitor

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(TEST_ID_ADDRESS, val * 10)
        tx = await monitor.confirmation_queue.get()

        body = {
            "from": TEST_ID_ADDRESS,
            "to": addr,
            "value": val
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)

        tx = sign_transaction(body['tx'], TEST_ID_KEY)

        body = {
            "tx": tx
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        # wait until the transaction is confirmed
        got_unconfirmed = 0
        while True:
            tx = await monitor.confirmation_queue.get()
            if tx['hash'] == tx_hash:
                if tx['blockNumber'] is None:
                    got_unconfirmed += 1
                else:
                    break

        # make sure we got an unconfirmed tx notification
        self.assertEqual(got_unconfirmed, 1)

        await monitor.shutdown()

class TestSendGCMPushNotification(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    def config_monitor(self):
        app = self._app
        self.mockpushclient = MockPushClient()
        monitor = tokeneth.monitor.BlockMonitor(app.connection_pool, app.config['ethereum']['url'],
                                                gcm_pushclient=self.mockpushclient)
        app.monitor = monitor
        return app

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_get_single_push_notification(self):

        self.config_monitor()

        # register for GCM PNs
        body = {
            "registration_id": TEST_GCM_ID
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ID_ADDRESS)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

        # register for notifications for the test address
        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }
        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        value = 2821181018869341261
        tx_hash = await self.faucet(TEST_WALLET_ADDRESS, value)

        unconfirmed_count = 0
        while True:
            token, payload = await self.mockpushclient.send_queue.get()

            self.assertEqual(token, TEST_GCM_ID)

            message = parse_sofa_message(payload['message'])

            self.assertIsInstance(message, SofaPayment)
            self.assertEqual(message['value'], hex(value))
            self.assertEqual(message['txHash'], tx_hash)

            if message['status'] == "confirmed":
                break

            self.assertEqual(message['status'], "unconfirmed")
            unconfirmed_count += 1
            if unconfirmed_count > 1:
                warnings.warn("got more than one unconfirmed notification for a single transaction")

        await self._app.monitor.shutdown()

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    async def test_get_multiple_push_notification(self):

        self.config_monitor()

        # register for GCM PNs
        body = {
            "registration_id": TEST_GCM_ID
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)
        # register 2nd device
        body = {
            "registration_id": TEST_GCM_ID_2
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM gcm_registrations WHERE token_id = $1", TEST_ID_ADDRESS)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)

        # register for notifications for the test address
        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }
        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        value = 2821181018869341261
        tx_hash = await self.faucet(TEST_WALLET_ADDRESS, value)

        unconfirmed_counts = {TEST_GCM_ID: 0, TEST_GCM_ID_2: 0}
        while True:
            token, payload = await self.mockpushclient.send_queue.get()

            self.assertTrue(token in (TEST_GCM_ID, TEST_GCM_ID_2))

            message = parse_sofa_message(payload['message'])

            self.assertIsInstance(message, SofaPayment)
            self.assertEqual(message['value'], hex(value))
            self.assertEqual(message['txHash'], tx_hash)

            if message['status'] == "confirmed":
                break

            self.assertEqual(message['status'], "unconfirmed")
            unconfirmed_counts[token] += 1
            if unconfirmed_counts[token] > 1:
                warnings.warn("got more than one unconfirmed notification for a single device")

        await self._app.monitor.shutdown()
