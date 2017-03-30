import asyncio
import time
import tornado.httpclient
from tornado.ioloop import IOLoop
from tokenservices.jsonrpc.client import JsonRPCClient
from tokenservices.sofa import SofaPayment
from tokenservices.log import logging
from tokenservices.database import DatabaseMixin

from tokenservices.utils import parse_int

from .handlers import BalanceMixin

DEFAULT_BLOCK_CHECK_DELAY = 0
DEFAULT_POLL_DELAY = 5

log = logging.getLogger("tokeneth.monitor")


JSONRPC_ERRORS = (tornado.httpclient.HTTPError,
                  ConnectionRefusedError,  # Server isn't running
                  OSError,  # No route to host
                 )

class BlockMonitor(DatabaseMixin, BalanceMixin):

    def __init__(self, pool, url, pushclient=None, ioloop=None):
        self.pool = pool
        self.eth = JsonRPCClient(url)
        self.pushclient = pushclient

        if ioloop is None:
            ioloop = IOLoop.current()
        self.ioloop = ioloop

        self._check_schedule = None
        self._poll_schedule = None
        self._block_checking_process = None
        self._filter_poll_process = None

        self._lastlog = 0

    def start(self):
        if not hasattr(self, '_startup_future'):
            self._startup_future = asyncio.Future()
            self.ioloop.add_callback(self._initialise)
            # run sanity check
            self.ioloop.add_callback(self.sanity_check)
        return self._startup_future

    async def _initialise(self):
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

        # list of callbacks for transaction notifications
        # form is {token_id: [method, ...], ...}
        self.callbacks = {}

        self.unmatched_transactions = {}

        await self.register_filters()

        self.schedule_filter_poll()

        self._startup_future.set_result(True)

    async def register_filters(self):
        if not self._shutdown:
            self._new_pending_transaction_filter_id = await self.register_new_pending_transaction_filter()
        if not self._shutdown:
            self._new_block_filter_id = await self.register_new_block_filter()

    async def register_new_pending_transaction_filter(self):
        backoff = 0
        while not self._shutdown:
            try:
                filter_id = await self.eth.eth_newPendingTransactionFilter()
                log.info("Listening for new pending transactions")
                return filter_id
            except:
                log.exception("Error registering for new pending transactions")
                if not self._shutdown:
                    backoff = min(backoff + 1, 10)
                    await asyncio.sleep(backoff)

    async def register_new_block_filter(self):
        backoff = 0
        while not self._shutdown:
            try:
                filter_id = await self.eth.eth_newBlockFilter()
                log.info("Listening for new blocks")
                return filter_id
            except:
                log.exception("Error registering for new blocks")
                if not self._shutdown:
                    backoff = min(backoff + 1, 10)
                    await asyncio.sleep(backoff)

    def schedule_block_check(self, delay=DEFAULT_BLOCK_CHECK_DELAY):

        self._check_schedule = self.ioloop.add_timeout(self.ioloop.time() + delay, self.block_check)

    def schedule_filter_poll(self, delay=DEFAULT_POLL_DELAY):

        self._poll_schedule = self.ioloop.add_timeout(self.ioloop.time() + delay, self.filter_poll)

    async def block_check(self):

        if self._block_checking_process is not None:
            log.debug("Block check is already running")
            return

        self._block_checking_process = asyncio.Future()

        while not self._shutdown:
            try:
                block = await self.eth.eth_getBlockByNumber(self.last_block_number + 1)
            except JSONRPC_ERRORS:
                log.exception("Error getting block by number")
                block = None
            if block:
                if self._lastlog + 1800 < time.time():
                    self._lastlog = time.time()
                    log.info("Processing block {}".format(block['number']))

                # process block
                for tx in block['transactions']:

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
        # force block check in 10 seconds
        self.schedule_block_check(10)

    async def filter_poll(self):

        if self._filter_poll_process is not None:
            log.debug("filter polling is already running")
            return

        self._filter_poll_process = asyncio.Future()
        new_pending_transactions = None
        if not self._shutdown:

            if self._new_pending_transaction_filter_id is not None:
                # get the list of new pending transactions
                try:
                    new_pending_transactions = await self.eth.eth_getFilterChanges(self._new_pending_transaction_filter_id)
                    # add any to the list of unprocessed transactions
                    self.unmatched_transactions.update({tx_hash: 0 for tx_hash in new_pending_transactions})
                except JSONRPC_ERRORS:
                    log.exception("WARNING: unable to connect to server")

        if new_pending_transactions is None:
            await self.register_filters()

        # go through all the unmatched transactions that have no match
        for tx_hash, age in list(self.unmatched_transactions.items()):
            try:
                tx = await self.eth.eth_getTransactionByHash(tx_hash)
            except JSONRPC_ERRORS:
                log.exception("Error getting transaction")
                tx = None
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

            if self._new_block_filter_id is not None:
                try:
                    new_blocks = await self.eth.eth_getFilterChanges(self._new_block_filter_id)
                except JSONRPC_ERRORS:
                    log.exception("Error getting new block filter")
                    new_blocks = None
                if new_blocks is None:
                    await self.register_filters()
                    # do a block check right after as it may have taken some time to
                    # reconnect and we may have missed a block notification
                    new_blocks = True
                # NOTE: this is not very smart, as if the block check is
                # already running this will cause it to run twice. However,
                # this is currently taken care of in the block check itself
                # which should suffice.
                if new_blocks and not self._shutdown:
                    self.schedule_block_check()

        self._filter_poll_process.set_result(True)
        self._filter_poll_process = None

        if not self._shutdown:
            self.schedule_filter_poll(1 if self.unmatched_transactions else DEFAULT_POLL_DELAY)

    async def shutdown(self):

        self._shutdown = True
        if self._check_schedule:
            self.ioloop.remove_timeout(self._check_schedule)
        if self._poll_schedule:
            self.ioloop.remove_timeout(self._poll_schedule)

        if self._block_checking_process:
            await self._block_checking_process
        if self._filter_poll_process:
            await self._filter_poll_process

        self._startup_future = None

    async def send_transaction_notifications(self, transaction):

        to_address = transaction['to']
        from_address = transaction['from']

        token_ids = []
        overwritten_tx_hash = None
        overwritten_tx_hash_to_address = None
        overwritten_tx_token_ids = []
        sender_token_id = None
        async with self.pool.acquire() as con:
            # find if we have a record of this tx by checking the from address and nonce
            db_txs = await con.fetch("SELECT * FROM transactions WHERE "
                                     "from_address = $1 AND nonce = $2",
                                     from_address, parse_int(transaction['nonce']))
            if len(db_txs) > 1:
                # see if one has the same hash
                db_tx = await con.fetchrow("SELECT * FROM transactions WHERE "
                                           "from_address = $1 AND nonce = $2 AND transaction_hash = $3 AND (last_status != $4 OR last_status IS NULL)",
                                           from_address, parse_int(transaction['nonce']), transaction['hash'], 'error')
                if db_tx is None:
                    # find if there are any that aren't marked as error
                    no_error = await con.fetch("SELECT * FROM transactions WHERE "
                                               "from_address = $1 AND nonce = $2 AND transaction_hash != $3 AND (last_status != $4 OR last_status IS NULL)",
                                               from_address, parse_int(transaction['nonce']), transaction['hash'], 'error')
                    if len(no_error) == 1:
                        db_tx = no_error[0]
                    elif len(no_error) != 0:
                        log.warning("Multiple transactions from '{}' exist with nonce '{}' in unknown state")

            elif len(db_txs) == 1:
                db_tx = db_txs[0]
            else:
                db_tx = None

            to_ids = await con.fetch("SELECT token_id FROM notification_registrations WHERE eth_address = $1", to_address)
            to_ids = [row['token_id'] for row in to_ids]
            token_ids.extend(to_ids)

            from_ids = await con.fetch("SELECT token_id FROM notification_registrations WHERE eth_address = $1", from_address)
            from_ids = [row['token_id'] for row in from_ids]
            token_ids.extend(from_ids)

            # check if we have this transaction saved in the database with
            # a specific token_id for the sender
            if db_tx:
                sender_token_id = db_tx['sender_token_id']
                # make sure the tx hash we have already matches this
                if transaction['hash'] != db_tx['transaction_hash']:
                    # only worry about this if it wasn't set as an error already
                    if db_tx['last_status'] != 'error':
                        log.warning("found overwritten transaction!")
                        log.warning("tx from: {}".format(from_address))
                        log.warning("nonce: {}".format(parse_int(transaction['nonce'])))
                        log.warning("old tx hash: {}".format(db_tx['transaction_hash']))
                        log.warning("new tx hash: {}".format(transaction['hash']))
                        log.warning("old tx status: {}".format(db_tx['last_status']))

                        # if not we have an overwrite
                        overwritten_tx_hash = db_tx['transaction_hash']
                        overwritten_tx_hash_to_address = db_tx['to_address']
                        if to_address != overwritten_tx_hash_to_address:
                            old_to_ids = await con.fetch("SELECT token_id FROM notification_registrations WHERE eth_address = $1", db_tx['to_address'])
                            old_to_ids = [row['token_id'] for row in old_to_ids]
                            overwritten_tx_token_ids.extend(old_to_ids)
                        else:
                            overwritten_tx_token_ids.extend(to_ids)
                        overwritten_tx_token_ids.extend(from_ids)
                        await con.execute("UPDATE transactions SET error = 1, last_status = 'error' WHERE transaction_hash = $1", db_tx['transaction_hash'])
                    # reset db_tx so we store the new tx later on
                    db_tx = None
                else:
                    new_status = 'error' if 'error' in transaction else ('confirmed' if transaction['blockNumber'] is not None else 'unconfirmed')
                    # check last_status to see if we need to send this as a pn or not
                    # and update the status
                    if db_tx['last_status'] == new_status:
                        # we've already sent a notification for this state
                        # so there's no need to send another
                        return
                    log.info("setting {} status on transaction: {}".format(new_status, transaction['hash']))
                    await con.execute("UPDATE transactions SET {}"
                                      "last_status = $1 "
                                      "WHERE transaction_hash = $2".format(
                                          "confirmed = (now() AT TIME ZONE 'utc'), " if new_status == 'confirmed' else ''),
                                      new_status,
                                      transaction['hash'])

            # if there are interested parties in the new tx, add it to the database
            if db_tx is None and (token_ids or any(addr in self.callbacks for addr in [to_address, from_address])):
                log.info("storing info about new interesting transaction")
                log.warning("tx from: {}".format(from_address))
                log.warning("nonce: {}".format(transaction['nonce']))
                log.warning("new tx hash: {}".format(transaction['hash']))

                # this could potentially be an overwrite of an overwrite putting the original
                # transaction back in as a non-error, in this case the last_status value is
                # simply updated from the original tx
                await con.execute(
                    "INSERT INTO transactions "
                    "(transaction_hash, from_address, to_address, nonce, value, estimated_gas_cost, sender_token_id, last_status) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                    "ON CONFLICT (transaction_hash) DO UPDATE "
                    "SET last_status = EXCLUDED.last_status",
                    transaction['hash'], from_address, to_address, parse_int(transaction['nonce']), str(parse_int(transaction['value'])),
                    str(parse_int(transaction['gas']) * parse_int(transaction['gasPrice'])),
                    sender_token_id, 'confirmed' if transaction['blockNumber'] is not None else 'unconfirmed')

        # if the tx was overwritten, send the tx cancel message first
        if overwritten_tx_hash:
            await self.send_notifications([overwritten_tx_hash_to_address, from_address],
                                          overwritten_tx_token_ids,
                                          {'hash': overwritten_tx_hash,
                                           'to': overwritten_tx_hash_to_address,
                                           'from': from_address,
                                           'blockNumber': None,
                                           'value': None,
                                           'error': 'overwritten'},
                                          sender_token_id)

        await self.send_notifications([to_address, from_address], token_ids, transaction, sender_token_id)

    async def send_notifications(self, addresses, token_ids, transaction, sender_token_id):

        # check websockets listening to addresses
        for subscription_id in addresses:
            if subscription_id in self.callbacks:
                log.info("Sending tx via websocket subscription: {}".format(subscription_id))
                for callback in self.callbacks[subscription_id]:
                    callback(subscription_id, transaction, sender_token_id)

        # for each token_id involved, see if they are registered for push notifications
        for token_id in token_ids:
            # check PN registrations for the token_id
            async with self.pool.acquire() as con:
                pn_registrations = await con.fetch("SELECT service, registration_id FROM push_notification_registrations WHERE token_id = $1", token_id)

            for row in pn_registrations:
                await self.send_push_notification(row['service'], row['registration_id'], transaction, token_id, sender_token_id)

    async def send_push_notification(self, push_service, registration_id, transaction, target_token_id, sender_token_id):
        if self.pushclient is None:
            return

        payment = SofaPayment.from_transaction(transaction)
        message = payment.render()

        try:
            log.info("Sending {} push notification to {} ({})".format(push_service, target_token_id, registration_id))
            return await self.pushclient.send(target_token_id, push_service, registration_id, {"message": message})
        except Exception:
            # TODO: think about adding retrying functionality in here
            log.exception("failed to send Push Notification")

    def subscribe(self, eth_address, callback):
        """Registers a callback to receive transaction notifications for the
        given token identifier.

        The callback must accept 2 parameters, the transaction dict, and the
        sender's token identifier"""
        callbacks = self.callbacks.setdefault(eth_address, [])
        if callback not in callbacks:
            callbacks.append(callback)

    def unsubscribe(self, eth_address, callback):
        if eth_address in self.callbacks and callback in self.callbacks[eth_address]:
            self.callbacks[eth_address].remove(callback)
            if not self.callbacks[eth_address]:
                self.callbacks.pop(eth_address)

    async def sanity_check(self):

        async with self.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT * FROM transactions WHERE (last_status = 'unconfirmed' OR last_status IS NULL) "
                "AND created < (now() AT TIME ZONE 'utc') - interval '5 minutes'"
            )
        for row in rows:
            # check if the transaction is still in the network
            tx = await self.eth.eth_getTransactionByHash(row['transaction_hash'])
            if tx is None:
                # the transaction no longer exists on the network
                await self.send_transaction_notifications({
                    'hash': row['transaction_hash'],
                    'to': row['to_address'],
                    'from': row['from_address'],
                    'blockNumber': None,
                    'value': row['value'],
                    'nonce': row['nonce'],
                    'gas': row['estimated_gas_cost'],
                    'gasPrice': '0x1',
                    'error': True})
            else:
                # check if the transactions has actually been confirmed
                if tx['blockNumber'] is not None:
                    await self.send_transaction_notifications(tx)
                else:
                    # update the nonce if it doesn't match what is in the database
                    # (NOTE: this shouldn't really be the case ever, this is mainly
                    # for checking on tx's from before the nonce was stored)
                    if parse_int(tx['nonce']) != row['nonce']:
                        log.info("Fixing stored nonce value for tx: {} (old: {}, new: {})".format(
                            tx['hash'], row['nonce'], parse_int(tx['nonce'])))
                        async with self.pool.acquire() as con:
                            await con.execute("UPDATE transactions SET nonce = $1 WHERE transaction_hash = $2",
                                              parse_int(tx['nonce']), row['transaction_hash'])
                    # log that there is an error
                    log.error("Found long standing unconfirmed transaction: {}".format(tx['hash']))
