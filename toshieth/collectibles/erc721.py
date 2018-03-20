import logging
from toshi.log import configure_logger
from toshi.ethereum.utils import data_decoder
from ethereum.abi import decode_abi, process_type, decode_single
from toshi.utils import parse_int
from toshieth.collectibles.base import CollectiblesTaskManager

log = logging.getLogger("toshieth.erc721")

class ERC721TaskManager(CollectiblesTaskManager):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        configure_logger(log)
        self._processing = {}

    async def process_block(self):
        async with self.connection_pool.acquire() as con:
            latest_block_number = await con.fetchval(
                "SELECT blocknumber FROM last_blocknumber")
        if latest_block_number is None:
            log.warning("no blocks processed by block monitor yet")
            return

        async with self.connection_pool.acquire() as con:
            contract_addresses = await con.fetch(
                "SELECT contract_address FROM collectibles WHERE type = 1 OR type = 721")

        for row in contract_addresses:
            self.ioloop.add_callback(self.process_block_for_contract, row['contract_address'])

    async def process_block_for_contract(self, contract_address):
        if contract_address in self._processing:
            return

        self._processing[contract_address] = True

        async with self.connection_pool.acquire() as con:
            latest_block_number = await con.fetchval(
                "SELECT blocknumber FROM last_blocknumber")
            collectible = await con.fetchrow("SELECT * FROM collectibles WHERE contract_address = $1",
                                             contract_address)
            if collectible['type'] == 1:
                events = await con.fetch("SELECT * FROM collectible_transfer_events "
                                         "WHERE contract_address = $1",
                                         contract_address)
            elif collectible['type'] == 721:
                # use default erc721 event
                events = [{
                    'contract_address': contract_address,
                    'name': 'Transfer',
                    'topic_hash': '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
                    'arguments': ['address', 'address', 'uint256'],
                    'indexed_arguments': [False, False, False],
                    'to_address_offset': 1,
                    'token_id_offset': 2
                }]

        from_block_number = collectible['last_block'] + 1

        if latest_block_number < from_block_number:
            del self._processing[contract_address]
            return

        to_block_number = min(from_block_number + 1000, latest_block_number)

        topics = [e['topic_hash'] for e in events]

        while True:
            try:
                logs = await self.eth.eth_getLogs(
                    fromBlock=from_block_number, toBlock=to_block_number,
                    topics=[topics] if len(topics) > 1 else topics,
                    address=contract_address)
                break
            except:
                continue

        if len(logs):

            updates = []

            for _log in logs:
                indexed_data = _log['topics'][1:]
                event = next(e for e in events if e['topic_hash'] == _log['topics'][0])
                data_types = [t for t, i in zip(event['arguments'], event['indexed_arguments']) if i is False]
                try:
                    data = decode_abi(data_types, data_decoder(_log['data']))
                except:
                    log.exception("Error decoding log data: {} {}".format(data_types, _log['data']))
                    del self._processing[contract_address]
                    return
                arguments = []
                try:
                    for t, i in zip(event['arguments'], event['indexed_arguments']):
                        if i is True:
                            arguments.append(decode_single(process_type(t), data_decoder(indexed_data.pop(0))))
                        else:
                            arguments.append(data.pop(0))
                except:
                    log.exception("Error compiling event data")

                to_address = arguments[event['to_address_offset']]
                token_id = parse_int(arguments[event['token_id_offset']])

                log.debug("{} #{} -> {} -> {}".format(collectible['name'], token_id,
                                                      event['name'], to_address))
                token_image = self.config['collectibles']['image_format'].format(
                    contract_address=contract_address,
                    token_id=token_id)
                updates.append((contract_address, hex(token_id), to_address, token_image))

            async with self.connection_pool.acquire() as con:
                await con.executemany(
                    "INSERT INTO collectible_tokens (contract_address, token_id, owner_address, image) "
                    "VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (contract_address, token_id) DO UPDATE "
                    "SET owner_address = EXCLUDED.owner_address",
                    updates)

        ready = collectible['ready'] or to_block_number == latest_block_number

        self.last_block = to_block_number
        async with self.connection_pool.acquire() as con:
            await con.execute("UPDATE collectibles SET last_block = $1, ready = $2 WHERE contract_address = $3",
                              to_block_number, ready, contract_address)

        del self._processing[contract_address]
        #log.info("Processed blocks #{} -> #{} for {} in {} seconds".format(
        #    from_block_number, to_block_number, collectible['name'], time.time() - starttime))
        if to_block_number < latest_block_number:
            self.ioloop.add_callback(self.process_block_for_contract, contract_address)

if __name__ == "__main__":
    app = ERC721TaskManager()
    app.run()
