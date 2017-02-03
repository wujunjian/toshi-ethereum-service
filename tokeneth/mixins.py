
class BalanceMixin:

    async def get_balances(self, eth_address, ignore_pending_recieved=False):
        """Gets the confirmed balance of the eth address from the ethereum network
        and adjusts the value based off any pending transactions.

        The option to ignore the pending recieved transactions is used to
        prevent the server from allowing the user to create transactions based on
        a potential balance that may not be available yet
        """
        confirmed_balance = await self.eth.eth_getBalance(eth_address)

        async with self.db:
            pending_sent = await self.db.fetch(
                "SELECT value, estimated_gas_cost FROM transactions WHERE confirmed IS NULL AND from_address = $1",
                eth_address)

        if pending_sent and len(pending_sent) > 0:
            pending_sent = sum(int(p['value']) + int(p['estimated_gas_cost']) for p in pending_sent)
        else:
            pending_sent = 0

        if ignore_pending_recieved is False:
            async with self.db:
                pending_recieved = await self.db.fetch(
                    "SELECT value FROM transactions WHERE confirmed IS NULL AND to_address = $1",
                    eth_address)
            if pending_recieved and len(pending_recieved) > 0:
                pending_recieved = sum(int(p['value']) for p in pending_recieved)
            else:
                pending_recieved = 0
        else:
            pending_recieved = 0

        balance = confirmed_balance + pending_recieved - pending_sent

        return confirmed_balance, balance
