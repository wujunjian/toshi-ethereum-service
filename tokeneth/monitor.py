import asyncio
from tornado.ioloop import IOLoop
from asyncbb.ethereum.client import JsonRPCClient
from tokenbrowser.sofa import SofaPayment
from asyncbb.log import logging

DEFAULT_BLOCK_CHECK_DELAY = 0
DEFAULT_POLL_DELAY = 5

log = logging.getLogger("tokeneth.monitor")

class BlockMonitor:

    def __init__(self, pool, url, gcm_pushclient=None, ioloop=None):
        self.pool = pool
        self.eth = JsonRPCClient(url)
        self.gcm_pushclient = gcm_pushclient

        if ioloop is None:
            ioloop = IOLoop.current()
        self.ioloop = ioloop

        self.ioloop.add_callback(self.initialise)

    async def initialise(self):
        # check what the last block number checked was last time this was started
        async with self.pool.acquire() as con:
            row = await con.fetchrow("SELECT blocknumber FROM last_blocknumber")
        if row is None:
            # if there was no previous start, get the current block number
            # and start from there
            last_block_number = await self.eth.eth_blockNumber()
            async with self.pool.acquire() as con:
                await con.execute("INSERT INTO last_blocknumber VALUES ($1)", last_block_number)
        else:
            last_block_number = row['blocknumber']

        self.last_block_number = last_block_number
        self._shutdown = False

        self.unmatched_transactions = {}

        # NOTE: filters timeout if eth_getFilterChanges is not used for some time
        # so there's no need to worry about removing them
        self._new_pending_transaction_filter_id = await self.eth.eth_newPendingTransactionFilter()
        self._new_block_filter_id = await self.eth.eth_newBlockFilter()

        # list of callbacks for transaction notifications
        # form is {token_id: [method, ...], ...}
        self.callbacks = {}

        self.schedule_filter_poll()

    def schedule_block_check(self, delay=DEFAULT_BLOCK_CHECK_DELAY):

        self._check_schedule = self.ioloop.add_timeout(self.ioloop.time() + delay, self.block_check)

    def schedule_filter_poll(self, delay=DEFAULT_POLL_DELAY):

        self._poll_schedule = self.ioloop.add_timeout(self.ioloop.time() + delay, self.filter_poll)

    async def block_check(self):

        self._block_checking_process = asyncio.Future()

        while not self._shutdown:
            block = await self.eth.eth_getBlockByNumber(self.last_block_number + 1)
            if block:

                # process block
                for tx in block['transactions']:

                    # if we had this stored as an unconfirmed transaction then mark it confirmed
                    tx_hash = tx['hash']
                    async with self.pool.acquire() as con:
                        row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1",
                                                 tx_hash)
                        if row:
                            await con.execute("UPDATE transactions SET confirmed = (now() AT TIME ZONE 'utc') WHERE transaction_hash = $1",
                                              tx_hash)

                    # send notifications to sender and reciever
                    await self.send_transaction_notifications(tx)

                self.last_block_number += 1
                async with self.pool.acquire() as con:
                    await con.execute("UPDATE last_blocknumber SET blocknumber = $1",
                                      self.last_block_number)

            else:

                break

        self._block_checking_process.set_result(True)
        self._block_checking_process = None

    async def filter_poll(self):

        self._filter_poll_process = asyncio.Future()
        if not self._shutdown:

            # get the list of new pending transactions
            new_pending_transactions = await self.eth.eth_getFilterChanges(self._new_pending_transaction_filter_id)
            # add any to the list of unprocessed transactions
            self.unmatched_transactions.update({tx_hash: 0 for tx_hash in new_pending_transactions})

        # go through all the unmatched transactions that have no match
        for tx_hash, age in list(self.unmatched_transactions.items()):
            tx = await self.eth.eth_getTransactionByHash(tx_hash)
            if tx is None:
                # if the tx has been checked a number of times and not found, assume it was
                # removed from the network before being accepted into a block
                if age >= 10:
                    self.unmatched_transactions.pop(tx_hash, None)
                else:
                    # increase the age
                    self.unmatched_transactions[tx_hash] += 1
            else:
                self.unmatched_transactions.pop(tx_hash, None)
                # check if the transaction has already been included in a block
                # and if so, ignore this notification as it will be picked up by
                # the confirmed block check and there's no need to send two
                # notifications about it
                if tx['blockNumber'] is not None:
                    continue

                await self.send_transaction_notifications(tx)

            if self._shutdown:
                break

        if not self._shutdown:

            new_blocks = await self.eth.eth_getFilterChanges(self._new_block_filter_id)
            if new_blocks and not self._shutdown:
                self.schedule_block_check()

        self._filter_poll_process.set_result(True)
        self._filter_poll_process = None

        if not self._shutdown:
            self.schedule_filter_poll(1 if self.unmatched_transactions else DEFAULT_POLL_DELAY)

    async def shutdown(self):

        self._shutdown = True
        self.ioloop.remove_timeout(self._check_schedule)
        self.ioloop.remove_timeout(self._poll_schedule)

        if self._block_checking_process:
            await self._block_checking_process
        if self._filter_poll_process:
            await self._filter_poll_process

    async def send_transaction_notifications(self, transaction):

        to_address = transaction['to']
        from_address = transaction['from']

        token_ids = []
        async with self.pool.acquire() as con:
            if to_address:
                to_ids = await con.fetch("SELECT token_id FROM notification_registrations WHERE eth_address = $1", to_address)
                token_ids.extend(to_ids)
            if from_address:
                from_ids = await con.fetch("SELECT token_id FROM notification_registrations WHERE eth_address = $1", from_address)
                token_ids.extend(from_ids)

            db_tx = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1",
                                       transaction['hash'])

        # check if we have this transaction saved in the database with
        # a specific token_id for the sender
        if db_tx:
            sender_token_id = db_tx['sender_token_id']
        else:
            sender_token_id = None

        # for each token_id involved, see if they are registered for push notifications

        for row in token_ids:
            token_id = row['token_id']
            # check PN registrations for the token_id
            async with self.pool.acquire() as con:
                # apn
                apn_ids = await con.fetch("SELECT apn_id FROM apn_registrations WHERE token_id = $1", token_id)
                # gcm
                gcm_ids = await con.fetch("SELECT gcm_id FROM gcm_registrations WHERE token_id = $1", token_id)

            for row in apn_ids:

                await self.send_apn(row['apn_id'], transaction, sender_token_id)

            for row in gcm_ids:

                await self.send_gcm(row['gcm_id'], transaction, sender_token_id)

    def send_apn(self, apn_id, transaction, sender_token_id):
        # TODO: format transaction and send APN
        pass

    def send_gcm(self, gcm_id, transaction, sender_token_id):
        if transaction['blockNumber'] is None:
            status = "unconfirmed"
        else:
            status = "confirmed"
        payment = SofaPayment(value=transaction['value'], txHash=transaction['hash'], status=status)
        message = payment.render()

        try:
            return self.gcm_pushclient.send(gcm_id, {"message": message})
        except Exception:
            # TODO: think about adding retrying functionality in here
            log.exception("failed to send Push Notification")

    def register(self, token_id, callback):
        """Registers a callback to receive transaction notifications for the
        given token identifier.

        The callback must accept 2 parameters, the transaction dict, and the
        sender's token identifier"""
        callbacks = self.callbacks.setdefault(token_id, [])
        if callback not in callbacks:
            callbacks.append(callback)

    def deregister(self, token_id, callback):
        if token_id in self.callbacks and callback in self.callbacks[token_id]:
            self.callbacks[token_id].remove(callback)
