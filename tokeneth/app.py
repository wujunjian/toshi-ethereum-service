import asyncbb.web
import os

from . import handlers
from .monitor import BlockMonitor

from tokenservices.handlers import GenerateTimestamp

urls = [
    (r"^/v1/tx/skel/?$", handlers.TransactionSkeletonHandler),
    (r"^/v1/tx/?$", handlers.SendTransactionHandler),
    (r"^/v1/tx/(0x[0-9a-fA-F]{64})/?$", handlers.TransactionHandler),
    (r"^/v1/balance/(0x[0-9a-fA-F]{40})/?$", handlers.BalanceHandler),
    (r"^/v1/timestamp/?$", GenerateTimestamp),
    (r"^/v1/register/?$", handlers.TransactionNotificationRegistrationHandler),
    (r"^/v1/deregister/?$", handlers.TransactionNotificationDeregistrationHandler),
    (r"^/v1/(apn|gcm)/register/?$", handlers.PNRegistrationHandler),
    (r"^/v1/(apn|gcm)/deregister/?$", handlers.PNDeregistrationHandler)
]

class Application(asyncbb.web.Application):

    def process_config(self):
        config = super(Application, self).process_config()

        if 'ETHEREUM_NODE_URL' in os.environ:
            config['ethereum'] = {'url': os.environ['ETHEREUM_NODE_URL']}

        return config


def main():

    app = Application(urls)
    app.monitor = BlockMonitor(app.connection_pool, app.config['ethereum']['url'])
    app.start()
