import asyncio
import binascii
from toshi.jsonrpc.handlers import JsonRPCBase, map_jsonrpc_arguments
from toshi.jsonrpc.errors import JsonRPCInvalidParamsError, JsonRPCError
from toshi.analytics import AnalyticsMixin
from toshi.database import DatabaseMixin
from toshi.ethereum.mixin import EthereumMixin
from toshi.redis import RedisMixin
from toshi.ethereum.utils import data_decoder, data_encoder, checksum_validate_address
from ethereum.exceptions import InvalidTransaction
from toshi.tasks import TaskDispatcher
from functools import partial
from toshi.utils import (
    validate_address, parse_int, validate_signature, validate_transaction_hash
)
from toshi.ethereum.tx import (
    DEFAULT_STARTGAS, DEFAULT_GASPRICE, create_transaction,
    encode_transaction, decode_transaction, is_transaction_signed,
    signature_from_transaction, add_signature_to_transaction,
    transaction_to_json, calculate_transaction_hash
)
from toshi.ethereum.utils import personal_ecrecover

from toshi.log import log

from .mixins import BalanceMixin
from .utils import RedisLock, RedisLockException, database_transaction_to_rlp_transaction

class JsonRPCInsufficientFundsError(JsonRPCError):
    def __init__(self, *, request=None, data=None):
        super().__init__(request.get('id') if request else None,
                         -32000, "Insufficient Funds", data,
                         'id' not in request if request else False)


class ToshiEthJsonRPC(JsonRPCBase, BalanceMixin, DatabaseMixin, EthereumMixin, AnalyticsMixin, RedisMixin):

    def __init__(self, user_toshi_id, application, request):
        self.user_toshi_id = user_toshi_id
        self.application = application
        self.request = request

    @property
    def tasks(self):
        if not hasattr(self, '_task_dispatcher'):
            self._task_dispatcher = TaskDispatcher(self.application.task_listener)
        return self._task_dispatcher

    @property
    def network_id(self):
        return parse_int(self.application.config['ethereum']['network_id'])

    async def get_balance(self, address):

        if not validate_address(address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_address', 'message': 'Invalid Address'})

        confirmed, unconfirmed, _, _ = await self.get_balances(address)

        return {
            "confirmed_balance": hex(confirmed),
            "unconfirmed_balance": hex(unconfirmed)
        }

    async def get_transaction_count(self, address):

        if not validate_address(address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_address', 'message': 'Invalid Address'})

        # get the network nonce
        nw_nonce = await self.eth.eth_getTransactionCount(address)

        # check the database for queued txs
        async with self.db:
            nonce = await self.db.fetchval(
                "SELECT nonce FROM transactions "
                "WHERE from_address = $1 "
                "AND (status is NULL OR status = 'queued' OR status = 'unconfirmed') "
                "ORDER BY nonce DESC",
                address)

        #nonce = nonce[0]['nonce'] if nonce else None
        if nonce is not None:
            # return the next usable nonce
            nonce = nonce + 1
            if nonce < nw_nonce:
                return nw_nonce
            return nonce
        else:
            return nw_nonce

    @map_jsonrpc_arguments({'from': 'from_address', 'to': 'to_address'})
    async def create_transaction_skeleton(self, *, to_address, from_address, value=0, nonce=None, gas=None, gas_price=None, data=None, token_address=None):

        if not validate_address(from_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_from_address', 'message': 'Invalid From Address'})

        if to_address is not None and not validate_address(to_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Invalid To Address'})

        if from_address != from_address.lower() and not checksum_validate_address(from_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_from_address', 'message': 'Invalid From Address Checksum'})

        if to_address is not None and to_address != to_address.lower() and not checksum_validate_address(to_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Invalid To Address Checksum'})

        # check if we should ignore the given gasprice
        # NOTE: only meant to be here while cryptokitty fever is pushing
        # up gas prices... this shouldn't be perminant
        # anytime the nonce is also set, use the provided gas (this is to
        # support easier overwriting of transactions)
        if gas_price is not None and nonce is None:
            async with self.db:
                whitelisted = await self.db.fetchrow("SELECT 1 FROM from_address_gas_price_whitelist WHERE address = $1", from_address)
                if not whitelisted:
                    whitelisted = await self.db.fetchrow("SELECT 1 FROM to_address_gas_price_whitelist WHERE address = $1", to_address)
            if not whitelisted:
                gas_price = None

        if gas_price is None:
            # try and use cached gas station gas price
            gas_station_gas_price = self.redis.get('gas_station_standard_gas_price')
            if gas_station_gas_price:
                gas_price = parse_int(gas_station_gas_price)
            if gas_price is None:
                gas_price = self.application.config['ethereum'].getint('default_gasprice', DEFAULT_GASPRICE)
        else:
            gas_price = parse_int(gas_price)
            if gas_price is None:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_gas_price', 'message': 'Invalid Gas Price'})

        if gas is not None:
            gas = parse_int(gas)
            if gas is None:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_gas', 'message': 'Invalid Gas'})

        if nonce is None:
            # check cache for nonce
            nonce = await self.get_transaction_count(from_address)
        else:
            nonce = parse_int(nonce)
            if nonce is None:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Invalid Nonce'})

        if data is not None:
            if isinstance(data, int):
                data = hex(data)
            if isinstance(data, str):
                try:
                    data = data_decoder(data)
                except binascii.Error:
                    pass
            if not isinstance(data, bytes):
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_data', 'message': 'Invalid Data field'})
        else:
            data = b''

        # flag to force arguments into an erc20 token transfer
        if token_address is not None:
            if not validate_address(token_address):
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_token_address', 'message': 'Invalid Token Address'})
            if data != b'':
                raise JsonRPCInvalidParamsError(data={'id': 'bad_arguments', 'message': 'Cannot include both data and token_address'})

            if isinstance(value, str) and value.lower() == "max":
                # get the balance in the database
                async with self.db:
                    value = await self.db.fetchval("SELECT value FROM token_balances "
                                                   "WHERE contract_address = $1 AND eth_address = $2",
                                                   token_address, from_address)
                if value is None:
                    # get the value from the ethereum node
                    data = "0x70a08231000000000000000000000000" + from_address[2:].lower()
                    value = await self.eth.eth_call(to_address=token_address, data=data)

            value = parse_int(value)
            if value is None or value < 0:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_value', 'message': 'Invalid Value'})
            data = data_decoder("0xa9059cbb000000000000000000000000{}{:064x}".format(to_address[2:].lower(), value))
            token_value = value
            value = 0
            to_address = token_address

        elif value:

            if value == "max":
                network_balance, balance, _, _ = await self.get_balances(from_address)
                if gas is None:
                    code = await self.eth.eth_getCode(to_address)
                    if code:
                        # we might have to do some work
                        try:
                            gas = await self.eth.eth_estimateGas(from_address, to_address, data=data, value=0)
                        except JsonRPCError:
                            # no fallback function implemented in the contract means no ether can be sent to it
                            raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Cannot send payments to that address'})
                        attempts = 0
                        # because the default function could do different things based on the eth sent, we make sure
                        # the value is suitable. if we get different values 3 times abort
                        while True:
                            if attempts > 2:
                                log.warning("Hit max attempts trying to get max value to send to contract '{}'".format(to_address))
                                raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Cannot send payments to that address'})
                            value = balance - (gas_price * gas)
                            try:
                                gas_with_value = await self.eth.eth_estimateGas(from_address, to_address, data=data, value=value)
                            except JsonRPCError:
                                # no fallback function implemented in the contract means no ether can be sent to it
                                raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Cannot send payments to that address'})
                            if gas_with_value != gas:
                                gas = gas_with_value
                                attempts += 1
                                continue
                            else:
                                break
                    else:
                        # normal address, 21000 gas per transaction
                        gas = 21000
                        value = balance - (gas_price * gas)
                else:
                    # preset gas, run with it!
                    value = balance - (gas_price * gas)
            else:
                value = parse_int(value)
                if value is None or value < 0:
                    raise JsonRPCInvalidParamsError(data={'id': 'invalid_value', 'message': 'Invalid Value'})

        if gas is None:
            try:
                gas = await self.eth.eth_estimateGas(from_address, to_address, data=data, value=value)
            except JsonRPCError:
                # this can occur if sending a transaction to a contract that doesn't match a valid method
                # and the contract has no default method implemented.
                # this can also happen if the current state of the blockchain means that submitting the
                # transaction would fail (abort).
                if token_address is not None:
                    # when dealing with erc20, this usually means the user's balance for that token isn't
                    # high enough, check that and throw an error if it's the case, and if not fall
                    # back to the standard invalid_data error
                    async with self.db:
                        bal = await self.db.fetchval("SELECT value FROM token_balances "
                                                     "WHERE contract_address = $1 AND eth_address = $2",
                                                     token_address, from_address)
                    if bal is not None:
                        bal = parse_int(bal)
                        if bal < token_value:
                            raise JsonRPCInsufficientFundsError(data={'id': 'insufficient_funds', 'message': 'Insufficient Funds'})
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_data', 'message': 'Unable to estimate gas for contract call'})
            # if data is present, buffer gas estimate by 20%
            if len(data) > 0:
                gas = int(gas * 1.2)

        try:
            tx = create_transaction(nonce=nonce, gasprice=gas_price, startgas=gas,
                                    to=to_address, value=value, data=data,
                                    network_id=self.network_id)
        except InvalidTransaction as e:
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_transaction', 'message': str(e)})

        if tx.intrinsic_gas_used > gas:
            raise JsonRPCInvalidParamsError(data={
                'id': 'invalid_transaction',
                'message': 'Transaction gas is too low. There is not enough gas to cover minimal cost of the transaction (minimal: {}, got: {}). Try increasing supplied gas.'.format(
                    tx.intrinsic_gas_used, gas)})

        transaction = encode_transaction(tx)

        return {"tx": transaction, "gas": hex(gas), "gas_price": hex(gas_price), "nonce": hex(nonce),
                "value": hex(token_value) if token_address else hex(value)}

    async def send_transaction(self, *, tx, signature=None):

        try:
            tx = decode_transaction(tx)
        except:
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_transaction', 'message': 'Invalid Transaction'})

        if is_transaction_signed(tx):

            tx_sig = data_encoder(signature_from_transaction(tx))

            if signature:

                if tx_sig != signature:

                    raise JsonRPCInvalidParamsError(data={
                        'id': 'invalid_signature',
                        'message': 'Invalid Signature: Signature in payload and signature of transaction do not match'
                    })
            else:

                signature = tx_sig
        else:

            if signature is None:
                raise JsonRPCInvalidParamsError(data={'id': 'missing_signature', 'message': 'Missing Signature'})

            if not validate_signature(signature):
                raise JsonRPCInvalidParamsError(data={
                    'id': 'invalid_signature',
                    'message': 'Invalid Signature: {}'.format(
                        'Invalid length' if len(signature) != 132 else 'Invalid hex value')
                })

            try:
                sig = data_decoder(signature)
            except Exception:
                log.exception("Unexpected error decoding valid signature: {}".format(signature))
                raise JsonRPCInvalidParamsError(data={
                    'id': 'invalid_signature',
                    'message': 'Invalid Signature'
                })

            add_signature_to_transaction(tx, sig)

        # validate network id, if it's not for "all networks"
        if tx.network_id is not None and self.network_id != tx.network_id:
            raise JsonRPCInvalidParamsError(data={
                'id': 'invalid_network_id',
                'message': 'Invalid Network ID'
            })

        from_address = data_encoder(tx.sender)
        to_address = data_encoder(tx.to)

        # prevent spamming of transactions with the same nonce from the same sender
        with RedisLock(self.redis, "{}:{}".format(from_address, tx.nonce),
                       raise_when_locked=partial(JsonRPCInvalidParamsError, data={'id': 'invalid_nonce', 'message': 'Nonce already used'}),
                       ex=5):

            # check for transaction overwriting
            async with self.db:
                existing = await self.db.fetchrow("SELECT * FROM transactions WHERE "
                                                  "from_address = $1 AND nonce = $2 AND "
                                                  "(status != 'error' or status is NULL)",
                                                  from_address, tx.nonce)

            # disallow transaction overwriting when the gas is lower or the transaction is confirmed
            if existing and (parse_int(existing['gas_price']) >= tx.gasprice or existing['status'] == 'confirmed'):
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Nonce already used'})

            # make sure the account has enough funds for the transaction
            network_balance, balance, _, _ = await self.get_balances(from_address)
            if existing:
                balance += parse_int(existing['value']) + parse_int(existing['gas']) * parse_int(existing['gas_price'])

            if balance < (tx.value + (tx.startgas * tx.gasprice)):
                raise JsonRPCInsufficientFundsError(data={'id': 'insufficient_funds', 'message': 'Insufficient Funds'})

            # validate the nonce (only necessary if tx doesn't already exist)
            if not existing:
                c_nonce = await self.get_transaction_count(from_address)

                if tx.nonce < c_nonce:
                    raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Provided nonce is too low'})
                if tx.nonce > c_nonce:
                    raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Provided nonce is too high'})

            if tx.intrinsic_gas_used > tx.startgas:
                raise JsonRPCInvalidParamsError(data={
                    'id': 'invalid_transaction',
                    'message': 'Transaction gas is too low. There is not enough gas to cover minimal cost of the transaction (minimal: {}, got: {}). Try increasing supplied gas.'.format(tx.intrinsic_gas_used, tx.startgas)})

            # now this tx fits enough of the criteria to allow it
            # onto the transaction queue
            tx_hash = calculate_transaction_hash(tx)

            if existing:
                log.info("Setting tx '{}' to error due to forced overwrite".format(existing['hash']))
                self.tasks.update_transaction(existing['transaction_id'], 'error')

            data = data_encoder(tx.data)
            if data and \
               ((data.startswith("0xa9059cbb") and len(data) == 138) or \
                (data.startswith("0x23b872dd") and len(data) == 202)):
                # check if the token is a known erc20 token
                async with self.db:
                    erc20_token = await self.db.fetchrow("SELECT * FROM tokens WHERE contract_address = $1",
                                                         to_address)
            else:
                erc20_token = False

            # add tx to database
            async with self.db:
                db_tx = await self.db.fetchrow(
                    "INSERT INTO transactions "
                    "(hash, from_address, to_address, nonce, "
                    "value, gas, gas_price, "
                    "data, v, r, s, "
                    "sender_toshi_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) "
                    "RETURNING transaction_id",
                    tx_hash, from_address, to_address, tx.nonce,
                    hex(tx.value), hex(tx.startgas), hex(tx.gasprice),
                    data_encoder(tx.data), hex(tx.v), hex(tx.r), hex(tx.s),
                    self.user_toshi_id)

                if erc20_token:
                    token_value = int(data[-64:], 16)
                    if data.startswith("0x23b872dd"):
                        erc20_from_address = "0x" + data[34:74]
                        erc20_to_address = "0x" + data[98:138]
                    else:
                        erc20_from_address = from_address
                        erc20_to_address = "0x" + data[34:74]
                    await self.db.execute(
                        "INSERT INTO token_transactions "
                        "(transaction_id, transaction_log_index, contract_address, from_address, to_address, value) "
                        "VALUES ($1, $2, $3, $4, $5, $6)",
                        db_tx['transaction_id'], 0, erc20_token['contract_address'],
                        erc20_from_address, erc20_to_address, hex(token_value))

                await self.db.commit()

            # trigger processing the transaction queue
            self.tasks.process_transaction_queue(from_address)
            # analytics
            # use notification registrations to try find toshi ids for users
            if self.user_toshi_id:
                sender_toshi_id = self.user_toshi_id
            else:
                async with self.db:
                    sender_toshi_id = await self.db.fetchval(
                        "SELECT toshi_id FROM notification_registrations WHERE "
                        "eth_address = $1",
                        from_address)
            async with self.db:
                receiver_toshi_id = await self.db.fetchval(
                    "SELECT toshi_id FROM notification_registrations WHERE "
                    "eth_address = $1",
                    to_address)
            self.track(sender_toshi_id, "Sent transaction")
            # it doesn't make sense to add user agent here as we
            # don't know the receiver's user agent
            self.track(receiver_toshi_id, "Received transaction", add_user_agent=False)

        return tx_hash

    async def get_transaction(self, tx_hash):

        if not validate_transaction_hash(tx_hash):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_transaction_hash', 'message': 'Invalid Transaction Hash'})

        tx = await self.eth.eth_getTransactionByHash(tx_hash)
        if tx is None:
            async with self.db:
                tx = await self.db.fetchrow(
                    "SELECT * FROM transactions WHERE "
                    "hash = $1 AND (status != 'error' OR status IS NULL) "
                    "ORDER BY transaction_id DESC",
                    tx_hash)
            if tx:
                tx = database_transaction_to_rlp_transaction(tx)
                tx = transaction_to_json(tx)
        return tx

    async def cancel_queued_transaction(self, tx_hash, signature):

        if not validate_transaction_hash(tx_hash):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_transaction_hash', 'message': 'Invalid Transaction Hash'})

        if not validate_signature(signature):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_signature', 'message': 'Invalid Signature'})

        async with self.db:
            tx = await self.db.fetchrow("SELECT * FROM transactions WHERE hash = $1 AND (status != 'error' OR status IS NULL)",
                                        tx_hash)
        if tx is None:
            raise JsonRPCError(None, -32000, "Transaction not found",
                               {'id': 'not_found', 'message': 'Transaction not found'})
        elif tx['status'] != 'queued' and tx['status'] is not None:
            raise JsonRPCError(None, -32000, "Transaction already sent to node",
                               {'id': 'invalid_transaction_status', 'message': 'Transaction already sent to node'})

        message = "Cancel transaction " + tx_hash
        if not personal_ecrecover(message, signature, tx['from_address']):
            raise JsonRPCError(None, -32000, "Permission Denied",
                               {'id': 'permission_denied', 'message': 'Permission Denied'})

        log.info("Setting tx '{}' to error due to user cancelation".format(tx['hash']))
        self.tasks.update_transaction(tx['transaction_id'], 'error')

    async def get_token_balances(self, eth_address, token_address=None):
        if not validate_address(eth_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_address', 'message': 'Invalid Address'})
        if token_address is not None and not validate_address(token_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_token_address', 'message': 'Invalid Token Address'})

        # get token balances
        while True:
            async with self.db:
                result = await self.db.execute("UPDATE token_registrations SET last_queried = (now() AT TIME ZONE 'utc') WHERE eth_address = $1", eth_address)
                await self.db.commit()
            registered = result == "UPDATE 1"

            if not registered:
                try:
                    with RedisLock(self.redis, "token_balance_update:{}".format(eth_address)):
                        try:
                            await self.tasks.update_token_cache("*", eth_address)
                        except:
                            log.exception("Error updating token cache")
                            raise
                        async with self.db:
                            await self.db.execute("INSERT INTO token_registrations (eth_address) VALUES ($1)", eth_address)
                            await self.db.commit()
                    break
                except RedisLockException:
                    # wait until the previous task is done and try again
                    await asyncio.sleep(0.1)
                    continue
            else:
                break

        if token_address:
            async with self.db:
                token = await self.db.fetchrow(
                    "SELECT symbol, name, decimals, format "
                    "FROM tokens WHERE contract_address = $1",
                    token_address)
                if token is None:
                    return None
                balance = await self.db.fetchval(
                    "SELECT value "
                    "FROM token_balances "
                    "WHERE eth_address = $1 AND contract_address = $2",
                    eth_address, token_address)
            if balance is None:
                balance = "0x0"
            details = {
                "symbol": token['symbol'],
                "name": token['name'],
                "decimals": token['decimals'],
                "value": balance,
                "contract_address": token_address
            }
            if token['format'] is not None:
                details["icon"] = "{}://{}/token/{}.{}".format(
                    self.request.protocol, self.request.host, token_address, token['format'])
            else:
                details['icon'] = None
            return details
        else:
            async with self.db:
                balances = await self.db.fetch(
                    "SELECT t.symbol, t.name, t.decimals, b.value, b.contract_address, t.format "
                    "FROM token_balances b "
                    "JOIN tokens t "
                    "ON t.contract_address = b.contract_address "
                    "WHERE eth_address = $1 ORDER BY t.symbol", eth_address)

            tokens = []
            for b in balances:
                details = {
                    "symbol": b['symbol'],
                    "name": b['name'],
                    "decimals": b['decimals'],
                    "value": b['value'],
                    "contract_address": b['contract_address']
                }
                if b['format'] is not None:
                    details["icon"] = "{}://{}/token/{}.{}".format(
                        self.request.protocol, self.request.host, b['contract_address'], b['format'])
                else:
                    details['icon'] = None

                tokens.append(details)

            return tokens

    async def get_collectibles(self, address, contract_address=None):

        if not validate_address(address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_address', 'message': 'Invalid Address'})
        if contract_address is not None and not validate_address(contract_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_contract_address', 'message': 'Invalid Contract Address'})

        if contract_address is None:
            async with self.db:
                collectibles = await self.db.fetch(
                    "SELECT t.contract_address, COUNT(t.token_id) AS value, c.name, c.icon, c.url "
                    "FROM collectible_tokens t "
                    "JOIN collectibles c ON c.contract_address = t.contract_address "
                    "WHERE t.owner_address = $1 AND c.ready = true "
                    "GROUP BY t.contract_address, c.name, c.icon, c.url",
                    address)

            return {"collectibles": [{
                "contract_address": c['contract_address'],
                "value": hex(c['value']),
                "name": c['name'],
                "url": c["url"],
                "icon": c['icon']
            } for c in collectibles]}
        else:
            async with self.db:
                collectible = await self.db.fetchrow(
                    "SELECT * FROM collectibles WHERE contract_address = $1 AND ready = true",
                    contract_address)
                tokens = await self.db.fetch(
                    "SELECT * FROM collectible_tokens "
                    "WHERE contract_address = $1 AND owner_address = $2",
                    contract_address, address)

            if collectible is None:
                return None
            return {
                "contract_address": collectible["contract_address"],
                "name": collectible["name"],
                "icon": collectible["icon"],
                "url": collectible["url"],
                "value": hex(len(tokens)),
                "tokens": [dict(t) for t in tokens]
            }
