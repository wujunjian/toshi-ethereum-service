import asyncio
from tornado.ioloop import IOLoop
from asyncbb.ethereum.client import JsonRPCClient

class BlockMonitor:

    def __init__(self, pool, url, ioloop=None):
        self.pool = pool
        self.eth = JsonRPCClient(url)

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
        self.schedule_block_check()

    def schedule_block_check(self):

        self._schudule = self.ioloop.add_timeout(self.ioloop.time() + 1, self.block_check)

    async def block_check(self):

        self._checking_process = asyncio.Future()

        while not self._shutdown:
            block = await self.eth.eth_getBlockByNumber(self.last_block_number + 1)
            if block:

                # process block
                for tx in block['transactions']:

                    tx_hash = tx['hash']
                    to_addr = tx['to']
                    from_addr = tx['from']

                    async with self.pool.acquire() as con:
                        row = await con.fetchrow("SELECT * FROM transactions WHERE transaction_hash = $1",
                                                 tx_hash)
                        if row:
                            await con.execute("UPDATE transactions SET confirmed = (now() AT TIME ZONE 'utc') WHERE transaction_hash = $1",
                                              tx_hash)

                    # send notifications to sender and reciever
                    await self.send_confirmation_notifications(tx)

                self.last_block_number += 1
                async with self.pool.acquire() as con:
                    await con.execute("UPDATE last_blocknumber SET blocknumber = $1",
                                      self.last_block_number)

            else:

                break

        self._checking_process.set_result(True)
        self._checking_process = None

        if not self._shutdown:
            self.schedule_block_check()

    async def shutdown(self):

        self._shutdown = True
        self.ioloop.remove_timeout(self._schudule)

        if self._checking_process:
            await self._checking_process

    async def send_confirmation_notifications(self, transaction):
        # TODO: send out notifications about the transaction confirmation
        # to anyone who's registered as interested
        pass
