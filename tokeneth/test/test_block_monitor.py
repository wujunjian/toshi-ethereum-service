import asyncio
import tokeneth.monitor
import warnings

from tornado.escape import json_decode
from tornado.testing import gen_test
from asyncbb.test.database import requires_database
from asyncbb.test.redis import requires_redis
from asyncbb.ethereum.client import JsonRPCClient
from asyncbb.ethereum.test.parity import requires_parity
from asyncbb.ethereum.test.faucet import FaucetMixin
from tokenbrowser.tx import sign_transaction, decode_transaction, signature_from_transaction
from tokenbrowser.sofa import SofaPayment, parse_sofa_message
from ethutils import data_encoder

from tokeneth.test.base import requires_block_monitor, EthServiceBaseTest

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

    async def send(self, token_id, network, device_token, data):
        if len(data) > 1 or 'message' not in data:
            raise NotImplementedError("Only data key allowed is 'message'")

        self.send_queue.put_nowait((device_token, data))

class MockPushClientBlockMonitor(tokeneth.monitor.BlockMonitor):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, pushclient=MockPushClient(), **kwargs)

class SimpleMonitorTest(FaucetMixin, EthServiceBaseTest):

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
        self.assertNotEqual(got_unconfirmed, 0)


class TestSendGCMPushNotification(FaucetMixin, EthServiceBaseTest):

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
    @requires_parity(pass_ethminer=True)
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_always_get_unconfirmed_push_notification(self, *, ethminer, monitor):
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
        for iteration in range(4):

            if iteration > 2:
                ethminer.pause()

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

            tx = decode_transaction(tx)
            self.assertEqual(tx_hash, data_encoder(tx.hash))

            if iteration > 2:
                await asyncio.sleep(5)
                ethminer.start()

            async with self.pool.acquire() as con:
                rows = await con.fetch("SELECT * FROM transactions WHERE nonce = $1", tx.nonce)
            self.assertEqual(len(rows), 1)

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

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_sending_transactions_and_storing_the_hash_correctly(self, *, monitor):

        """This test is born out of the fact that `UnsingedTransaction.hash` always
        calculates the hash of the transaction without the signature, even after `.sign`
        has been called on the transaction. This caused errors in what was being stored
        in the database and incorrectly detecting transaction overwrites.

        This test exposed the behaviour correctly so that it could be fixed"""

        # register for GCM PNs
        body = {
            "registration_id": TEST_GCM_ID
        }
        resp = await self.fetch_signed("/gcm/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)
        # register for notifications for the test address
        body = {
            "addresses": [TEST_ID_ADDRESS]
        }
        resp = await self.fetch_signed("/register", signing_key=TEST_ID_KEY, method="POST", body=body)
        self.assertResponseCodeEqual(resp, 204, resp.body)

        body = {
            "from": FAUCET_ADDRESS,
            "to": TEST_ID_ADDRESS,
            "value": 10 ** 10
        }

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertEqual(resp.code, 200)

        body = json_decode(resp.body)
        tx = decode_transaction(body['tx'])
        tx = sign_transaction(tx, FAUCET_PRIVATE_KEY)
        sig = signature_from_transaction(tx)

        body = {
            "tx": body['tx'],
            "signature": data_encoder(sig)
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertEqual(resp.code, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']

        async def check_db():
            async with self.pool.acquire() as con:
                rows = await con.fetch("SELECT * FROM transactions WHERE nonce = $1", tx.nonce)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['transaction_hash'], tx_hash)
            if rows[0]['last_status'] is not None:
                self.assertEqual(rows[0]['last_status'], 'unconfirmed')
            self.assertIsNone(rows[0]['error'])

        await self.wait_on_tx_confirmation(tx_hash, check_db)
        while True:
            token, payload = await monitor.pushclient.send_queue.get()
            message = parse_sofa_message(payload['message'])

            self.assertIsInstance(message, SofaPayment)
            self.assertEqual(message['txHash'], tx_hash)

            if message['status'] == "confirmed":
                break

class MonitorErrorsTest(FaucetMixin, EthServiceBaseTest):

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=SimpleBlockMonitor, pass_monitor=True, begin_started=False)
    async def test_get_block_confirmation(self, *, parity, ethminer, monitor):

        """Tests that the block monitor recovers correctly after errors
        in the ethereum node"""

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

class TestTransactionOverwrites(FaucetMixin, EthServiceBaseTest):

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_transaction_overwrite(self, *, ethminer, parity, monitor):
        # make sure no blocks are confirmed for the meantime
        ethminer.pause()

        # set up pn registrations
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO notification_registrations (token_id, eth_address) VALUES ($1, $2)",
                              TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)
            await con.fetch("INSERT INTO push_notification_registrations (service, registration_id, token_id) VALUES ($1, $2, $3)",
                            'gcm', TEST_GCM_ID, TEST_ID_ADDRESS)

        # get tx skeleton
        tx1 = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, 10 ** 18)
        tx2 = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, 0)
        self.assertEqual(decode_transaction(tx1).nonce, decode_transaction(tx2).nonce)
        # sign and send
        tx1_hash = await self.sign_and_send_tx(FAUCET_PRIVATE_KEY, tx1)

        # wait for tx PN
        pn = await monitor.pushclient.send_queue.get()
        # allow other processing to complete
        await asyncio.sleep(0.5)

        # send tx2 manually
        rpcclient = JsonRPCClient(parity.dsn()['url'])
        tx2_hash = await rpcclient.eth_sendRawTransaction(sign_transaction(tx2, FAUCET_PRIVATE_KEY))

        # we expect 2 push notifications, one for the error of the
        # overwritten txz and one for the new tx
        pn = await monitor.pushclient.send_queue.get()
        pn = await monitor.pushclient.send_queue.get()
        # allow other processing to complete
        await asyncio.sleep(0.5)

        async with self.pool.acquire() as con:
            tx1_row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1", tx1_hash)
            tx2_row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1", tx2_hash)

        self.assertEqual(tx1_row['last_status'], 'error')
        self.assertEqual(tx2_row['last_status'], 'unconfirmed')

    @gen_test(timeout=60)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_transaction_overwrite_spam(self, *, ethminer, parity, monitor):

        no_to_spam = 10

        # make sure no blocks are confirmed
        ethminer.pause()

        # set up pn registrations
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO notification_registrations (token_id, eth_address) VALUES ($1, $2)",
                              TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)
            await con.fetch("INSERT INTO push_notification_registrations (service, registration_id, token_id) VALUES ($1, $2, $3)",
                            'gcm', TEST_GCM_ID, TEST_ID_ADDRESS)

        # send initial tx
        tx1 = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, 10 ** 18)

        txs = []
        for i in range(no_to_spam):
            tx = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, i)
            txs.append(sign_transaction(tx, FAUCET_PRIVATE_KEY))

        tx1_hash = await self.sign_and_send_tx(FAUCET_PRIVATE_KEY, tx1)
        # wait for tx PN
        pn = await monitor.pushclient.send_queue.get()

        # spam send txs manually
        rpcclient = JsonRPCClient(parity.dsn()['url'])
        for ntx in txs:
            await rpcclient.eth_sendRawTransaction(ntx)
            # force the pending transaction filter polling to
            # run after each new transaction is posted
            await monitor.filter_poll()
            # we expect two pns for each overwrite
            pn = await monitor.pushclient.send_queue.get()
            pn = await monitor.pushclient.send_queue.get()

        async with self.pool.acquire() as con:
            tx1_row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1", tx1_hash)
            tx_rows = await con.fetchrow("SELECT COUNT(*) FROM transactions")
            tx_rows_error = await con.fetchrow("SELECT COUNT(*) FROM transactions WHERE last_status = 'error'")

        self.assertEqual(tx1_row['last_status'], 'error')

        self.assertEqual(tx_rows['count'], no_to_spam + 1)
        self.assertEqual(tx_rows_error['count'], no_to_spam)

    @gen_test(timeout=30)
    @requires_database
    @requires_redis
    @requires_parity(pass_args=True)
    @requires_block_monitor(cls=MockPushClientBlockMonitor, pass_monitor=True)
    async def test_resend_old_after_overwrite(self, *, ethminer, parity, monitor):
        # make sure no blocks are confirmed for the meantime
        ethminer.pause()

        # set up pn registrations
        async with self.pool.acquire() as con:
            await con.execute("INSERT INTO notification_registrations (token_id, eth_address) VALUES ($1, $2)",
                              TEST_ID_ADDRESS, TEST_WALLET_ADDRESS)
            await con.fetch("INSERT INTO push_notification_registrations (service, registration_id, token_id) VALUES ($1, $2, $3)",
                            'gcm', TEST_GCM_ID, TEST_ID_ADDRESS)

        # get tx skeleton
        tx1 = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, 10 ** 18)
        tx2 = await self.get_tx_skel(FAUCET_PRIVATE_KEY, TEST_WALLET_ADDRESS, 0)
        self.assertEqual(decode_transaction(tx1).nonce, decode_transaction(tx2).nonce)
        # sign and send
        tx1_hash = await self.sign_and_send_tx(FAUCET_PRIVATE_KEY, tx1)
        # wait for tx PN
        await monitor.pushclient.send_queue.get()

        # send tx2 manually
        rpcclient = JsonRPCClient(parity.dsn()['url'])
        tx2_hash = await rpcclient.eth_sendRawTransaction(sign_transaction(tx2, FAUCET_PRIVATE_KEY))
        await monitor.filter_poll()
        await monitor.pushclient.send_queue.get()
        await monitor.pushclient.send_queue.get()

        # resend tx1 manually
        tx1_hash = await rpcclient.eth_sendRawTransaction(sign_transaction(tx1, FAUCET_PRIVATE_KEY))
        await monitor.filter_poll()
        await monitor.pushclient.send_queue.get()
        await monitor.pushclient.send_queue.get()

        async with self.pool.acquire() as con:
            tx1_row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1", tx1_hash)
            tx2_row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1", tx2_hash)

        self.assertEqual(tx1_row['last_status'], 'unconfirmed')
        self.assertEqual(tx2_row['last_status'], 'error')
