from asyncbb.handlers import BaseHandler
from asyncbb.errors import JSONHTTPError, JsonRPCInternalError
from asyncbb.database import DatabaseMixin
from asyncbb.ethereum.mixin import EthereumMixin
from asyncbb.errors import JsonRPCError
from asyncbb.redis import RedisMixin

from tokenservices.handlers import RequestVerificationMixin

from .mixins import BalanceMixin
from .jsonrpc import TokenEthJsonRPC

class BalanceHandler(DatabaseMixin, EthereumMixin, BaseHandler):

    async def get(self, address):

        try:
            result = await TokenEthJsonRPC(None, self.application).get_balance(address)
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})

        self.write(result)

class TransactionSkeletonHandler(EthereumMixin, RedisMixin, BaseHandler):

    async def post(self):

        try:
            if 'from' in self.json:
                self.json['from_address'] = self.json.pop('from')
            if 'to' in self.json:
                self.json['to_address'] = self.json.pop('to')
            result = await TokenEthJsonRPC(None, self.application).create_transaction_skeleton(**self.json)
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})
        except TypeError:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        self.write({
            "tx": result
        })

class SendTransactionHandler(BalanceMixin, EthereumMixin, DatabaseMixin, RedisMixin, RequestVerificationMixin, BaseHandler):

    async def post(self):

        if self.is_request_signed():
            sender_token_id = self.verify_request()
        else:
            # this is an anonymous transaction
            sender_token_id = None

        try:
            result = await TokenEthJsonRPC(sender_token_id, self.application).send_transaction(**self.json)
        except JsonRPCInternalError as e:
            raise JSONHTTPError(500, body={'errors': [e.data]})
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})
        except TypeError:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        self.write({
            "tx_hash": result
        })

class TransactionHandler(EthereumMixin, BaseHandler):

    async def get(self, tx_hash):

        try:
            tx = await TokenEthJsonRPC(None, self.application).get_transaction(tx_hash)
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})

        if tx is None:
            raise JSONHTTPError(404, body={'error': [{'id': 'not_found', 'message': 'Not Found'}]})
        self.write(tx)

class TransactionNotificationRegistrationHandler(RequestVerificationMixin, DatabaseMixin, BaseHandler):

    async def post(self):

        token_id = self.verify_request()
        payload = self.json

        if 'addresses' not in payload or len(payload['addresses']) == 0:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        addresses = payload['addresses']

        try:
            await TokenEthJsonRPC(token_id, self.application).subscribe(*addresses)
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})
        except TypeError:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        self.set_status(204)

class TransactionNotificationDeregistrationHandler(RequestVerificationMixin, DatabaseMixin, BaseHandler):

    async def post(self):

        token_id = self.verify_request()
        payload = self.json

        if 'addresses' not in payload or len(payload['addresses']) == 0:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        addresses = payload['addresses']

        try:
            await TokenEthJsonRPC(token_id, self.application).unsubscribe(*addresses)
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})
        except TypeError:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        self.set_status(204)

class SubscriptionListHandler(RequestVerificationMixin, DatabaseMixin, BaseHandler):

    async def get(self):

        token_id = self.verify_request()
        try:
            result = await TokenEthJsonRPC(token_id, self.application).list_subscriptions()
        except JsonRPCError as e:
            raise JSONHTTPError(400, body={'errors': [e.data]})
        except TypeError:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        self.write({
            "subscriptions": result
        })

class PNRegistrationHandler(RequestVerificationMixin, DatabaseMixin, BaseHandler):

    async def post(self, service):

        token_id = self.verify_request()
        payload = self.json

        if 'registration_id' not in payload:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        # TODO: registration id verification

        async with self.db:

            await self.db.execute(
                "INSERT INTO push_notification_registrations (service, registration_id, token_id) VALUES ($1, $2, $3) ON CONFLICT (service, registration_id) DO UPDATE SET token_id = $3",
                service, payload['registration_id'], token_id)

            await self.db.commit()

        self.set_status(204)

class PNDeregistrationHandler(RequestVerificationMixin, DatabaseMixin, BaseHandler):

    async def post(self, service):

        token_id = self.verify_request()
        payload = self.json

        if 'registration_id' not in payload:
            raise JSONHTTPError(400, body={'errors': [{'id': 'bad_arguments', 'message': 'Bad Arguments'}]})

        # TODO: registration id verification

        async with self.db:

            await self.db.execute(
                "DELETE FROM push_notification_registrations WHERE service = $1 AND registration_id = $2 AND token_id = $3",
                service, payload['registration_id'], token_id)

            await self.db.commit()

        self.set_status(204)
