import asyncio
import logging

from tornado.platform.asyncio import to_asyncio_future
from toshi.log import configure_logger, log_unhandled_exceptions
from toshi.utils import parse_int

from toshi.database import DatabaseMixin
from toshi.ethereum.mixin import EthereumMixin

from toshi.tasks import TaskHandler
from toshieth.tasks import TaskListenerApplication

log = logging.getLogger("toshieth.erc20manager")

class ERC20UpdateHandler(DatabaseMixin, EthereumMixin, TaskHandler):

    @log_unhandled_exceptions(logger=log)
    async def update_token_cache(self, contract_address, *eth_addresses):

        if len(eth_addresses) == 0:
            return

        async with self.db:
            if contract_address == "*":
                tokens = await self.db.fetch("SELECT contract_address FROM tokens")
            else:
                tokens = [{'contract_address': contract_address}]

        futures = []
        for token in tokens:
            for address in eth_addresses:
                # data for `balanceOf(address)`
                data = "0x70a08231000000000000000000000000" + address[2:]
                f = to_asyncio_future(self.eth.eth_call(to_address=token['contract_address'], data=data))
                futures.append((token['contract_address'], address, f))

        # wait for all the jsonrpc calls to finish
        await asyncio.gather(*[f[2] for f in futures], return_exceptions=True)
        bulk_update = []
        bulk_delete = []
        for contract_address, eth_address, f in futures:
            try:
                value = f.result()
                # value of "0x" means something failed with the contract call
                if value == "0x0000000000000000000000000000000000000000000000000000000000000000" or value == "0x":
                    if value == "0x":
                        log.warning("calling balanceOf for contract {} failed".format(contract_address))
                    bulk_delete.append((contract_address, eth_address))
                else:
                    value = hex(parse_int(value))  # remove hex padding of value
                    bulk_update.append((contract_address, eth_address, value))
            except:
                log.exception("WARNING: failed to update token cache of '{}' for address: {}".format(contract_address, eth_address))
                continue
        async with self.db:
            await self.db.executemany("INSERT INTO token_balances (contract_address, eth_address, value) VALUES ($1, $2, $3) "
                                      "ON CONFLICT (contract_address, eth_address) DO UPDATE set value = EXCLUDED.value",
                                      bulk_update)
            await self.db.executemany("DELETE FROM token_balances WHERE contract_address = $1 AND eth_address = $2",
                                      bulk_delete)
            await self.db.commit()


class TaskManager(TaskListenerApplication):

    def __init__(self, *args, **kwargs):
        super().__init__([(ERC20UpdateHandler,)], *args, listener_id="erc20manager", **kwargs)
        configure_logger(log)

    def start(self):
        return super().start()

if __name__ == "__main__":
    app = TaskManager()
    app.run()
