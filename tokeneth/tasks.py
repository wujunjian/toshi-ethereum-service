import asyncio
import os
from tokenservices.tasks import TaskListener
from tokenservices.web import ConfigurationManager
from tokeneth.websocket import WebsocketNotificationHandler
from tornado.ioloop import IOLoop

class EthServiceTaskListener(TaskListener):
    def __init__(self, application, queue=None, ioloop=None):
        super().__init__([(WebsocketNotificationHandler,)],
                         application, queue=queue, ioloop=ioloop,
                         listener_id="ethservicetasklistener")

        self.callbacks = {}

    def subscribe(self, eth_address, callback):
        """Registers a callback to receive transaction notifications for the
        given token identifier.

        The callback must accept 2 parameters, the transaction dict, and the
        sender's token identifier"""
        callbacks = self.callbacks.setdefault(eth_address, [])
        if callback not in callbacks:
            callbacks.append(callback)

    def unsubscribe(self, eth_address, callback):
        if eth_address in self.callbacks and callback in self.callbacks[eth_address]:
            self.callbacks[eth_address].remove(callback)
            if not self.callbacks[eth_address]:
                self.callbacks.pop(eth_address)


class TaskListenerApplication(ConfigurationManager):

    def __init__(self, handlers, listener_id=None, config=None, redis_connection_pool=None, connection_pool=None, ioloop=None):

        if ioloop is None:
            ioloop = IOLoop.current()
        self.ioloop = ioloop

        if config:
            self.config = config
        else:
            self.config = self.process_config()

        # TODO: some nicer way of handling this
        # although the use cases for having this should only be in
        # testing, so it shouldn't be a huge issue
        if connection_pool is not None or redis_connection_pool is not None:
            self.redis_connection_pool = redis_connection_pool
            self.connection_pool = connection_pool
        else:
            self.prepare_databases(handle_migration=False)

        self.task_listener = TaskListener(
            handlers,
            self,
            listener_id=listener_id
        )

    def process_config(self):
        config = super().process_config()
        if 'ETHEREUM_NODE_URL' in os.environ:
            config['ethereum'] = {'url': os.environ['ETHEREUM_NODE_URL']}

        return config

    def start(self):
        return self.task_listener.start_task_listener()

    def shutdown(self, *, soft=False):
        return self.task_listener.stop_task_listener(soft=soft)

    def run(self):
        self.start()
        asyncio.get_event_loop().run_forever()
