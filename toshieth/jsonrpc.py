import binascii
from toshi.jsonrpc.handlers import JsonRPCBase, map_jsonrpc_arguments
from toshi.jsonrpc.errors import JsonRPCInvalidParamsError, JsonRPCError
from toshi.analytics import AnalyticsMixin
from toshi.database import DatabaseMixin
from toshi.ethereum.mixin import EthereumMixin
from toshi.redis import RedisMixin
from toshi.ethereum.utils import data_decoder, data_encoder
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

from toshi.log import log

from .mixins import BalanceMixin
from .utils import RedisLock, database_transaction_to_rlp_transaction

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
    async def create_transaction_skeleton(self, *, to_address, from_address, value=0, nonce=None, gas=None, gas_price=None, data=None):

        if not validate_address(from_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_from_address', 'message': 'Invalid From Address'})

        if to_address is not None and not validate_address(to_address):
            raise JsonRPCInvalidParamsError(data={'id': 'invalid_to_address', 'message': 'Invalid To Address'})

        if value:
            value = parse_int(value)
            if value is None or value < 0:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_value', 'message': 'Invalid Value'})

        # check optional arguments

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

        if gas is None:
            try:
                gas = await self.eth.eth_estimateGas(from_address, to_address, nonce=nonce, data=data)
            except JsonRPCError:
                # this can occur if sending a transaction to a contract that doesn't match a valid method
                # and the contract has no default method implemented
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_data', 'message': 'Invalid Data field'})
        else:
            gas = parse_int(gas)
            if gas is None:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_gas', 'message': 'Invalid Gas'})

        if gas_price is None:
            gas_price = self.application.config['ethereum'].getint('default_gasprice', DEFAULT_GASPRICE)
        else:
            gas_price = parse_int(gas_price)
            if gas_price is None:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_gas_price', 'message': 'Invalid Gas Price'})

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

        return transaction

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

            # disallow transaction overwriting for known transactions
            async with self.db:
                existing = await self.db.fetchrow("SELECT * FROM transactions WHERE "
                                                  "from_address = $1 AND nonce = $2 AND status != $3",
                                                  from_address, tx.nonce, 'error')
            if existing:
                # debugging checks
                existing_tx = await self.eth.eth_getTransactionByHash(existing['hash'])
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Nonce already used'})

            # make sure the account has enough funds for the transaction
            network_balance, balance, _, _ = await self.get_balances(from_address)

            #log.info("Attempting to send transaction\nHash: {}\n{} -> {}\nValue: {} + {} (gas) * {} (startgas) = {}\nSender's Balance {} ({} unconfirmed)".format(
            #    calculate_transaction_hash(tx), from_address, to_address, tx.value, tx.startgas, tx.gasprice, tx.value + (tx.startgas * tx.gasprice), network_balance, balance))

            if balance < (tx.value + (tx.startgas * tx.gasprice)):
                raise JsonRPCInsufficientFundsError(data={'id': 'insufficient_funds', 'message': 'Insufficient Funds'})

            # validate the nonce
            c_nonce = await self.get_transaction_count(from_address)

            if tx.nonce < c_nonce:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Provided nonce is too low'})
            if tx.nonce > c_nonce:
                raise JsonRPCInvalidParamsError(data={'id': 'invalid_nonce', 'message': 'Provided nonce is too high'})

            if tx.intrinsic_gas_used > tx.startgas:
                raise JsonRPCInvalidParamsError(data={
                    'id': 'invalid_transaction',
                    'message': 'Transaction gas is too low. There is not enough gas to cover minimal cost of the transaction (minimal: {}, got: {}). Try increasing supplied gas.'.format(
                        tx.intrinsic_gas_used, tx.startgas)})

            # now this tx fits enough of the criteria to allow it
            # onto the transaction queue
            tx_hash = calculate_transaction_hash(tx)

            # add tx to database
            async with self.db:
                await self.db.execute(
                    "INSERT INTO transactions "
                    "(hash, from_address, to_address, nonce, "
                    "value, gas, gas_price, "
                    "data, v, r, s, "
                    "sender_toshi_id) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
                    tx_hash, from_address, to_address, tx.nonce,
                    hex(tx.value), hex(tx.startgas), hex(tx.gasprice),
                    data_encoder(tx.data), hex(tx.v), hex(tx.r), hex(tx.s),
                    self.user_toshi_id)
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
