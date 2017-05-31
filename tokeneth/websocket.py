import asyncio
import os
import uuid
import time

import tornado.ioloop
import tornado.websocket
import tornado.web

from datetime import datetime
from tokenservices.database import DatabaseMixin
from tokenservices.handlers import RequestVerificationMixin
from tokenservices.utils import validate_address
from tokenservices.tasks import TaskHandler
from tokenservices.sofa import SofaPayment
from tokenservices.utils import parse_int

from tokenservices.log import log
from tokenservices.jsonrpc.errors import JsonRPCInvalidParamsError
from .jsonrpc import TokenEthJsonRPC

class WebsocketJsonRPCHandler(TokenEthJsonRPC):

    """Special handling for subscribe/unsubscribe when handled over
    websockets
    """

    def __init__(self, user_token_id, application, request_handler):
        super().__init__(user_token_id, application)
        self.request_handler = request_handler

    async def subscribe(self, *addresses):
        for address in addresses:
            if not validate_address(address):
                raise JsonRPCInvalidParamsError(data={'id': 'bad_arguments', 'message': 'Bad Arguments'})

        try:
            await self.request_handler.subscribe(addresses)
        except:
            log.exception("fuck")
            raise

        return True

    async def unsubscribe(self, *addresses):
        for address in addresses:
            if not validate_address(address):
                raise JsonRPCInvalidParamsError(data={'id': 'bad_arguments', 'message': 'Bad Arguments'})

        await self.request_handler.unsubscribe(addresses)

        return True

    def list_subscriptions(self):

        return list(self.request_handler.subscription_ids)

    def get_timestamp(self):
        return int(time.time())

    async def list_payment_updates(self, address, start_time, end_time=None):

        try:
            return (await self._list_payment_updates(address, start_time, end_time))
        except:
            import traceback
            traceback.print_exc()
            raise

    async def _list_payment_updates(self, address, start_time, end_time=None):

        if end_time is None:
            end_time = datetime.utcnow()
        elif not isinstance(end_time, datetime):
            end_time = datetime.utcfromtimestamp(end_time)
        if not isinstance(start_time, datetime):
            start_time = datetime.utcfromtimestamp(start_time)

        async with self.db:
            txs = await self.db.fetch(
                "SELECT * FROM transactions WHERE "
                "(from_address = $1 OR to_address = $1) AND "
                "updated > $2 AND updated < $3"
                "ORDER BY transaction_id ASC",
                address, start_time, end_time)
        payments = []
        for tx in txs:
            status = tx['status']
            if status is None or status == 'queued':
                status = 'unconfirmed'
            value = parse_int(tx['value'])
            if value is None:
                value = 0
            else:
                value = hex(value)
            # if the tx was created before the start time, send the unconfirmed
            # message as well.
            if status == 'confirmed' and tx['created'] > start_time:
                payments.append(SofaPayment(status='unconfirmed', txHash=tx['hash'],
                                            value=value, fromAddress=tx['from_address'],
                                            toAddress=tx['to_address']).render())
            payments.append(SofaPayment(status=status, txHash=tx['hash'],
                                        value=value, fromAddress=tx['from_address'],
                                        toAddress=tx['to_address']).render())

        return payments

class WebsocketHandler(tornado.websocket.WebSocketHandler, DatabaseMixin, RequestVerificationMixin):

    KEEP_ALIVE_TIMEOUT = 30

    @tornado.web.asynchronous
    def get(self, *args, **kwargs):

        self.user_token_id = self.verify_request()
        self.subscription_ids = set()
        return super().get(*args, **kwargs)

    def open(self):

        self.session_id = uuid.uuid4().hex
        self.io_loop = tornado.ioloop.IOLoop.current()
        self.schedule_ping()

    def on_close(self):
        if hasattr(self, '_pingcb'):
            self.io_loop.remove_timeout(self._pingcb)
        self.io_loop.add_callback(self.unsubscribe, self.subscription_ids)

    def schedule_ping(self):
        self._pingcb = self.io_loop.call_later(self.KEEP_ALIVE_TIMEOUT, self.send_ping)

    def send_ping(self):
        try:
            self.ping(os.urandom(1))
        except tornado.websocket.WebSocketClosedError:
            pass

    def on_pong(self, data):
        self.schedule_ping()

    async def _on_message(self, message):
        try:
            response = await WebsocketJsonRPCHandler(
                self.user_token_id, self.application, self)(message)
            if response:
                self.write_message(response)
        except:
            log.exception("unexpected error handling message: {}".format(message))
            raise

    def on_message(self, message):
        if message is None:
            return
        tornado.ioloop.IOLoop.current().add_callback(self._on_message, message)

    async def subscribe(self, addresses):
        async with self.db:
            for address in addresses:
                await self.db.execute(
                    "INSERT INTO notification_registrations (token_id, service, registration_id, eth_address) "
                    "VALUES ($1, $2, $3, $4) ON CONFLICT (token_id, service, registration_id, eth_address) DO NOTHING",
                    self.user_token_id, 'ws', self.session_id, address)
            await self.db.commit()

        for address in addresses:
            self.application.task_listener.subscribe(
                address, self.send_transaction_notification)
        self.subscription_ids.update(addresses)

    async def unsubscribe(self, addresses):
        self.subscription_ids.difference_update(addresses)
        async with self.db:
            for address in addresses:
                await self.db.execute(
                    "DELETE FROM notification_registrations WHERE token_id = $1 AND service = $2 AND registration_id = $3 AND eth_address = $4",
                    self.user_token_id, 'ws', self.session_id, address)
        for address in addresses:
            self.application.task_listener.unsubscribe(
                address, self.send_transaction_notification)

    def send_transaction_notification(self, subscription_id, message):

        # make sure things are still connected
        if self.ws_connection is None:
            return

        self.write_message({
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "subscription": subscription_id,
                "message": message
            }
        })

class WebsocketNotificationHandler(TaskHandler):

    async def send_notification(self, subscription_id, message):
        if subscription_id in self.application.callbacks:
            for callback in self.application.callbacks[subscription_id]:
                f = callback(subscription_id, message)
                if asyncio.iscoroutine(f):
                    await f
