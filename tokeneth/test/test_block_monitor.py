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
from ethutils import private_key_to_address

from tokeneth.test.test_transaction import (
    TEST_PRIVATE_KEY as TEST_ID_KEY,
    TEST_ADDRESS as TEST_ID_ADDRESS,
    TEST_PRIVATE_KEY_2 as TEST_WALLET_KEY,
    TEST_ADDRESS_2 as TEST_WALLET_ADDRESS
)
from asyncbb.ethereum.test.parity import FAUCET_PRIVATE_KEY, FAUCET_ADDRESS
from tokeneth.test.test_pn_registration import TEST_GCM_ID, TEST_GCM_ID_2

def requires_block_monitor(func=None, cls=tokeneth.monitor.BlockMonitor, pass_monitor=False, begin_started=True):
    """Used to ensure all database connections are returned to the pool
    before finishing the test"""

    def wrap(fn):

        async def wrapper(self, *args, **kwargs):

            if 'ethereum' not in self._app.config:
                raise Exception("Missing ethereum config from setup")

            self._app.monitor = cls(self._app.connection_pool, self._app.config['ethereum']['url'])

            if begin_started:
                await self._app.monitor.start()

            if pass_monitor:
                if pass_monitor is True:
                    kwargs['monitor'] = self._app.monitor
                else:
                    kwargs[pass_monitor] = self._app.monitor

            try:
                f = fn(self, *args, **kwargs)
                if asyncio.iscoroutine(f):
                    await f
            finally:
                await self._app.monitor.shutdown()

        return wrapper

    if func is not None:
        return wrap(func)
    else:
        return wrap

class SimpleBlockMonitor(tokeneth.monitor.BlockMonitor):

    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.confirmation_queue = asyncio.Queue()

    async def send_transaction_notifications(self, transaction):
        self.confirmation_queue.put_nowait(transaction)

class MockPushClient:

    def __init__(self):
        self.send_queue = asyncio.Queue()

    async def send(self, token_id, network, device_token, data):

        if len(data) > 1 or 'message' not in data:
            raise NotImplementedError("Only data key allowed is 'message'")

        self.send_queue.put_nowait((device_token, data))

class MockPushClientBlockMonitor(tokeneth.monitor.BlockMonitor):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, pushclient=MockPushClient(), **kwargs)

class SimpleMonitorTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=SimpleBlockMonitor, pass_monitor=True)
    async def test_get_block_confirmation(self, *, parity, ethminer, monitor):

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

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)

        tx = sign_transaction(body['tx'], TEST_ID_KEY)

        body = {
            "tx": tx
        }

        # stop ethminer breifly to ensure we get an unconfirmed transaction
        # if this isn't done, then there is a chance that the block may be
        # mined before the unconfirmed transaction is seen meaning only
        # the confirmed version will be seen.
        ethminer.pause()

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        # wait until the transaction is confirmed
        got_unconfirmed = 0
        while True:
            tx = await monitor.confirmation_queue.get()
            if tx['hash'] == tx_hash:
                if tx['blockNumber'] is None:
                    got_unconfirmed += 1
                    # restart ethminer
                    ethminer.start()
                else:
                    break

        # make sure we got an unconfirmed tx notification
        self.assertEqual(got_unconfirmed, 1)


class TestSendGCMPushNotification(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_get_single_push_notification(self, *, monitor):

        # register for GCM PNs
        body = {
            "registration_id": TEST_GCM_ID
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM push_notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)
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
            token, payload = await monitor.pushclient.send_queue.get()

            self.assertEqual(token, TEST_GCM_ID)

            message = parse_sofa_message(payload['message'])

            self.assertIsInstance(message, SofaPayment)
            self.assertEqual(message['value'], hex(value))
            self.assertEqual(message['txHash'], tx_hash)

            if message['status'] == "confirmed":
                break

            self.assertEqual(message['status'], "unconfirmed")
            unconfirmed_count += 1
            # since this tx came from outside our system there's no guarantee we
            # even see this since the block monitor might miss it before it's
            # actually confirmed
            if unconfirmed_count > 1:
                warnings.warn("got more than one unconfirmed notification for a single transaction")

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_get_multiple_push_notification(self, *, monitor):

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
            rows = await con.fetch("SELECT * FROM push_notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)
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
            token, payload = await monitor.pushclient.send_queue.get()

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

    @gen_test(timeout=120)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_always_get_unconfirmed_push_notification(self, *, monitor):
        """Tests that when tx's are send through our systems we always get
        an unconfirmed push notification"""

        # register for GCM PNs
        body = {
            "registration_id": TEST_GCM_ID
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        async with self.pool.acquire() as con:
            rows = await con.fetch("SELECT * FROM push_notification_registrations WHERE token_id = $1", TEST_ID_ADDRESS)
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 1)

        # register for notifications for the test address
        body = {
            "addresses": [TEST_WALLET_ADDRESS]
        }
        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        # run this a bunch of times to see if
        # we can expose any race conditions
        for _ in range(10):

            value = 2821181018869341261

            resp = await self.fetch("/tx/skel", method="POST", body={
                "from": FAUCET_ADDRESS,
                "to": TEST_WALLET_ADDRESS,
                "value": value
            })
            self.assertResponseCodeEqual(resp, 200, resp.body)
            body = json_decode(resp.body)
            tx = sign_transaction(body['tx'], FAUCET_PRIVATE_KEY)
            resp = await self.fetch("/tx", method="POST", body={
                "tx": tx
            })
            self.assertResponseCodeEqual(resp, 200, resp.body)
            tx_hash = json_decode(resp.body)['tx_hash']

            unconfirmed_count = 0
            while True:
                token, payload = await monitor.pushclient.send_queue.get()

                self.assertEqual(token, TEST_GCM_ID)

                message = parse_sofa_message(payload['message'])

                self.assertIsInstance(message, SofaPayment)
                self.assertEqual(message['value'], hex(value))
                self.assertEqual(message['txHash'], tx_hash)

                if message['status'] == "confirmed":
                    break

                self.assertEqual(message['status'], "unconfirmed")
                unconfirmed_count += 1
            # when the tx is sent through our systems we should
            # always get one unconfirmed notification, and we
            # should never get more than one
            self.assertEqual(unconfirmed_count, 1)

class MonitorErrorsTest(FaucetMixin, AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    async def send_tx(self, from_key, to_addr, val):
        from_addr = private_key_to_address(from_key)
        body = {
            "from": from_addr,
            "to": to_addr,
            "value": val
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)

        tx = sign_transaction(body['tx'], TEST_ID_KEY)

        body = {
            "tx": tx
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']
        return tx_hash

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=SimpleBlockMonitor, pass_monitor=True, begin_started=False)
    async def test_get_block_confirmation(self, *, parity, ethminer, monitor):

        addr = '0x39bf9e501e61440b4b268d7b2e9aa2458dd201bb'
        val = 761751855997712

        await self.faucet(TEST_ID_ADDRESS, val * 10)

        await monitor.start()

        # kill the server!
        parity.pause()

        # sleep for a bit to give the monitor time to poll
        await asyncio.sleep(5)

        # start it again
        parity.start()

        # see if we get notifications still!

        await self.send_tx(TEST_ID_KEY, addr, 10 ** 10)
        tx = await monitor.confirmation_queue.get()

        self.assertIsNotNone(tx)
