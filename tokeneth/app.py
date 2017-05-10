import tokenservices.web
import os

from . import handlers
from . import websocket
from .tasks import EthServiceTaskListener

from tokenservices.handlers import GenerateTimestamp

from tokenservices.log import configure_logger
from tokenservices.log import log as services_log

urls = [
    (r"^/v1/tx/skel/?$", handlers.TransactionSkeletonHandler),
    (r"^/v1/tx/?$", handlers.SendTransactionHandler),
    (r"^/v1/tx/(0x[0-9a-fA-F]{64})/?$", handlers.TransactionHandler),
    (r"^/v1/balance/(0x[0-9a-fA-F]{40})/?$", handlers.BalanceHandler),
    (r"^/v1/timestamp/?$", GenerateTimestamp),
    (r"^/v1/(apn|gcm)/register/?$", handlers.PNRegistrationHandler),
    (r"^/v1/(apn|gcm)/deregister/?$", handlers.PNDeregistrationHandler),
    (r"^/v1/ws/?$", websocket.WebsocketHandler)
]

class Application(tokenservices.web.Application):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_listener = EthServiceTaskListener(self)

    def process_config(self):
        config = super().process_config()

        if 'ETHEREUM_NODE_URL' in os.environ:
            config['ethereum'] = {'url': os.environ['ETHEREUM_NODE_URL']}

        configure_logger(services_log)

        return config

    def start(self):
        self.task_listener.start_task_listener()
        super().start()

def main():
    app = Application(urls)
    app.start()
