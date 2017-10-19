import asyncio
import logging
import time
import tornado.httpclient
from toshi.jsonrpc.client import JsonRPCClient
from toshi.log import configure_logger
from toshi.database import DatabaseMixin
from toshi.tasks import TaskDispatcher

from toshi.utils import parse_int

from .tasks import TaskListenerApplication

DEFAULT_BLOCK_CHECK_DELAY = 0
DEFAULT_POLL_DELAY = 5

log = logging.getLogger("toshieth.monitor")

JSONRPC_ERRORS = (tornado.httpclient.HTTPError,
                  ConnectionRefusedError,  # Server isn't running
                  OSError,  # No route to host
                 )

class BlockMonitor(TaskListenerApplication):

    def __init__(self, *args, listener_id="block_monitor", **kwargs):

        # so DatabaseMixin works
        self.application = self

        super().__init__([], *args, listener_id=listener_id, **kwargs)

        configure_logger(log)

        if 'monitor' in self.config:
            node_url = self.config['monitor']['url']
        else:
            log.warning("monitor using config['ethereum'] node")
            node_url = self.config['ethereum']['url']

        self.eth = JsonRPCClient(node_url)

        self._check_schedule = None
        self._poll_schedule = None
        self._block_checking_process = None
        self._filter_poll_process = None

        self._lastlog = 0

        self.tasks = TaskDispatcher(self.task_listener)

    def start(self):
        if not hasattr(self, '_startup_future'):
            self._startup_future = asyncio.Future()
            self.ioloop.add_callback(self._initialise)
        return self._startup_future

    async def _initialise(self):
        # start the task listener
        await super().start()

        # check what the last block number checked was last time this was started
        async with self.connection_pool.acquire() as con:
            row = await con.fetchrow("SELECT blocknumber FROM last_blocknumber")
        if row is None:
            # if there was no previous start, get the current block number
            # and start from there
            last_block_number = await self.eth.eth_blockNumber()
            async with self.connection_pool.acquire() as con:
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

                for tx in block['transactions']:

                    # send notifications to sender and reciever
                    await self.process_transaction(tx)

                # check if there are logs
                if block['logsBloom'] != "0x" + ("0" * 512):
                    # get them all
                    logs = await self.eth.eth_getLogs(fromBlock=block['number'], toBlock=block['number'])
                    # send notifications for anyone registered
                    async with self.connection_pool.acquire() as con:
                        for event in logs:
                            for topic in event['topics']:
                                filters = await con.fetch(
                                    "SELECT * FROM filter_registrations WHERE contract_address = $1 AND topic_id = $2",
                                    event['address'], topic)
                                for filter in filters:
                                    self.tasks.send_filter_notification(
                                        filter['filter_id'], filter['topic'], event['data'])

                self.last_block_number += 1
                async with self.connection_pool.acquire() as con:
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

                await self.process_transaction(tx)

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

    async def shutdown(self, *, soft=False):

        self._shutdown = True
        if self._check_schedule:
            self.ioloop.remove_timeout(self._check_schedule)
        if self._poll_schedule:
            self.ioloop.remove_timeout(self._poll_schedule)

        if self._block_checking_process:
            await self._block_checking_process
        if self._filter_poll_process:
            await self._filter_poll_process

        await super().shutdown(soft=soft)

        self._startup_future = None

    async def process_transaction(self, transaction):

        to_address = transaction['to']
        # make sure we use a valid encoding of "empty" for contract deployments
        if to_address is None:
            to_address = "0x"
        from_address = transaction['from']

        async with self.connection_pool.acquire() as con:
            # find if we have a record of this tx by checking the from address and nonce
            db_txs = await con.fetch("SELECT * FROM transactions WHERE "
                                     "from_address = $1 AND nonce = $2",
                                     from_address, parse_int(transaction['nonce']))
            if len(db_txs) > 1:
                # see if one has the same hash
                db_tx = await con.fetchrow("SELECT * FROM transactions WHERE "
                                           "from_address = $1 AND nonce = $2 AND hash = $3 AND (status != $4 OR status IS NULL)",
                                           from_address, parse_int(transaction['nonce']), transaction['hash'], 'error')
                if db_tx is None:
                    # find if there are any that aren't marked as error
                    no_error = await con.fetch("SELECT * FROM transactions WHERE "
                                               "from_address = $1 AND nonce = $2 AND hash != $3 AND (status != $4 OR status IS NULL)",
                                               from_address, parse_int(transaction['nonce']), transaction['hash'], 'error')
                    if len(no_error) == 1:
                        db_tx = no_error[0]
                    elif len(no_error) != 0:
                        log.warning("Multiple transactions from '{}' exist with nonce '{}' in unknown state")

            elif len(db_txs) == 1:
                db_tx = db_txs[0]
            else:
                db_tx = None

            # if we have a previous transaction, do some checking to see what's going on
            if db_tx:
                # see if this is an overwritten transaction
                if db_tx['hash'] != transaction['hash']:
                    # if the status of the old tx was previously an error, we don't care about it
                    # otherwise, we have to notify the interested parties of the overwrite
                    if db_tx['status'] != 'error':

                        log.warning("found overwritten transaction!")
                        log.warning("tx from: {}".format(from_address))
                        log.warning("nonce: {}".format(parse_int(transaction['nonce'])))
                        log.warning("old tx hash: {}".format(db_tx['hash']))
                        log.warning("new tx hash: {}".format(transaction['hash']))

                        self.tasks.update_transaction(db_tx['transaction_id'], 'error')
                    # fall through to the "new transaction" code
                else:
                    self.tasks.update_transaction(
                        db_tx['transaction_id'],
                        'confirmed' if transaction['blockNumber'] is not None else 'unconfirmed')
                    return

            # check if there is input, and if the input reprensents an erc20
            # `transfer(address,uint256)` or `transferFrom(address,address,uint256)`
            # method signature
            if transaction['input'] and \
               ((transaction['input'].startswith("0xa9059cbb") and len(transaction['input']) == 138) or \
                (transaction['input'].startswith("0x23b872dd") and len(transaction['input']) == 202)):
                # check if the token is a known erc20 token
                erc20_token = await con.fetchrow("SELECT * FROM tokens WHERE address = $1",
                                                 to_address)
                if erc20_token:

                    if transaction['input'].startswith("0x23b872dd"):
                        # `transferFrom(address,address,uint256)` token sender is
                        # the first argument and token receiver is the second
                        # argument in the input data

                        # NOTE: not sure what is really sensible here, `transferFrom`
                        # is triggered by having someone give someone else permission
                        # to transfer their tokens. So the tokens are coming from an
                        # address that is different to the address of the transaction
                        # sender. This is not something we have a real use case for
                        # inside toshi yet, so leaving this behaviour as is for now.
                        # future implementations might be that we have a 3rd "interested"
                        # party to send notifications to

                        interested_from_address = "0x" + transaction['input'][34:74]
                        interested_to_address = "0x" + transaction['input'][98:138]
                    else:
                        # `transfer(address,uint256)` token receiver is the first argument in the input data
                        interested_from_address = from_address
                        interested_to_address = "0x" + transaction['input'][34:74]
            else:
                erc20_token = False
                interested_to_address = to_address
                interested_from_address = from_address

            # find out if there is anyone interested in this transaction
            is_interesting = await con.fetchrow("SELECT 1 FROM notification_registrations "
                                                "WHERE eth_address = $1 OR eth_address = $2",
                                                interested_to_address, interested_from_address)

            if is_interesting:
                # if so, add it to the database and trigger an update
                # add tx to database
                db_tx = await con.fetchrow(
                    "INSERT INTO transactions "
                    "(hash, from_address, to_address, nonce, "
                    "value, gas, gas_price, "
                    "data) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                    "RETURNING transaction_id",
                    transaction['hash'], from_address, to_address, parse_int(transaction['nonce']),
                    hex(parse_int(transaction['value'])), hex(parse_int(transaction['gas'])), hex(parse_int(transaction['gasPrice'])),
                    transaction['input'])

                if erc20_token:
                    # token transfer value is the final uint256 value in the transaction input data
                    token_value = int(transaction['input'][-64:], 16)
                    await con.execute(
                        "INSERT INTO erc20_transactions "
                        "(transaction_id, symbol, from_address, to_address, value) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        db_tx['transaction_id'], erc20_token['symbol'],
                        interested_from_address, interested_to_address, token_value)

                self.tasks.update_transaction(
                    db_tx['transaction_id'],
                    'confirmed' if transaction['blockNumber'] is not None else 'unconfirmed')


if __name__ == '__main__':

    monitor = BlockMonitor()
    monitor.start()
    asyncio.get_event_loop().run_forever()
