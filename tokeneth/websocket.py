import os

import tornado.ioloop
import tornado.websocket
import tornado.web

from asyncbb.database import DatabaseMixin
from tokenservices.handlers import RequestVerificationMixin
from tokenbrowser.utils import validate_address

from tokenservices.log import log
from asyncbb.errors import JsonRPCInvalidParamsError
from .jsonrpc import TokenEthJsonRPC

class WebsocketJsonRPCHandler(TokenEthJsonRPC):

    """Special handling for subscribe/unsubscribe when handled over
    websockets
    """

    def __init__(self, user_token_id, application, request_handler):
        super().__init__(user_token_id, application)
        self.request_handler = request_handler

    def subscribe(self, *addresses):
        for address in addresses:
            if not validate_address(address):
                raise JsonRPCInvalidParamsError(data={'id': 'bad_arguments', 'message': 'Bad Arguments'})

        self.request_handler.subscribe(addresses)

        return True

    def unsubscribe(self, *addresses):
        for address in addresses:
            if not validate_address(address):
                raise JsonRPCInvalidParamsError(data={'id': 'bad_arguments', 'message': 'Bad Arguments'})

        self.request_handler.unsubscribe(addresses)

        return True

    def list_subscriptions(self):

        return list(self.request_handler.subscription_ids)


class WebsocketHandler(tornado.websocket.WebSocketHandler, DatabaseMixin, RequestVerificationMixin):

    KEEP_ALIVE_TIMEOUT = 30

    @tornado.web.asynchronous
    def get(self, *args, **kwargs):

        self.user_token_id = self.verify_request()
        self.subscription_ids = set()
        return super().get(*args, **kwargs)

    def open(self):

        self.io_loop = tornado.ioloop.IOLoop.current()
        self.schedule_ping()

    def on_close(self):
        if hasattr(self, '_pingcb'):
            self.io_loop.remove_timeout(self._pingcb)
        self.unsubscribe(self.subscription_ids)

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

    def subscribe(self, addresses):
        self.subscription_ids.update(addresses)
        for address in addresses:
            self.application.monitor.subscribe(
                address, self.send_transaction_notification)

    def unsubscribe(self, addresses):
        self.subscription_ids.difference_update(addresses)
        for address in addresses:
            self.application.monitor.unsubscribe(
                address, self.send_transaction_notification)

    def send_transaction_notification(self, subscription_id, tx, sender_token_id=None):

        # make sure things are still connected
        if self.ws_connection is None:
            return

        self.write_message({
            "jsonrpc": "2.0",
            "method": "subscription",
            "params": {
                "subscription": subscription_id,
                "transaction": tx,
                "sender_token_id": sender_token_id
            }
        })
