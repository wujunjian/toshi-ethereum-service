import asyncbb.web
import os

from . import handlers
from .monitor import BlockMonitor, log as monitor_log
from tornado.log import app_log, access_log, gen_log
from . import websocket

from tokenservices.push import PushServerClient, GCMHttpPushClient
from tokenservices.handlers import GenerateTimestamp
from configparser import SectionProxy

from asyncbb.log import SlackLogHandler
from tokenservices.log import log

urls = [
    (r"^/v1/tx/skel/?$", handlers.TransactionSkeletonHandler),
    (r"^/v1/tx/?$", handlers.SendTransactionHandler),
    (r"^/v1/tx/(0x[0-9a-fA-F]{64})/?$", handlers.TransactionHandler),
    (r"^/v1/balance/(0x[0-9a-fA-F]{40})/?$", handlers.BalanceHandler),
    (r"^/v1/timestamp/?$", GenerateTimestamp),
    (r"^/v1/register/?$", handlers.TransactionNotificationRegistrationHandler),
    (r"^/v1/deregister/?$", handlers.TransactionNotificationDeregistrationHandler),
    (r"^/v1/subscriptions/?$", handlers.SubscriptionListHandler),
    (r"^/v1/(apn|gcm)/register/?$", handlers.PNRegistrationHandler),
    (r"^/v1/(apn|gcm)/deregister/?$", handlers.PNDeregistrationHandler),
    (r"^/v1/ws/?$", websocket.WebsocketHandler)
]

class Application(asyncbb.web.Application):

    def process_config(self):
        config = super(Application, self).process_config()

        if 'ETHEREUM_NODE_URL' in os.environ:
            config['ethereum'] = {'url': os.environ['ETHEREUM_NODE_URL']}

        if 'PUSH_URL' in os.environ:
            config.setdefault('pushserver', SectionProxy(config, 'pushserver'))['url'] = os.environ['PUSH_URL']
        if 'PUSH_PASSWORD' in os.environ:
            config.setdefault('pushserver', SectionProxy(config, 'pushserver'))['password'] = os.environ['PUSH_PASSWORD']
        if 'PUSH_USERNAME' in os.environ:
            config.setdefault('pushserver', SectionProxy(config, 'pushserver'))['username'] = os.environ['PUSH_USERNAME']

        if 'GCM_SERVER_KEY' in os.environ:
            config.setdefault('gcm', SectionProxy(config, 'gcm'))['server_key'] = os.environ['GCM_SERVER_KEY']

        if 'SLACK_LOG_URL' in os.environ:
            handler = SlackLogHandler({'default': os.environ['SLACK_LOG_URL']})
            log.addHandler(handler)
            monitor_log.addHandler(handler)
            app_log.addHandler(handler)
            gen_log.addHandler(handler)
            access_log.addHandler(handler)

        return config

def main():

    app = Application(urls)
    if 'ethereum' in app.config and 'url' in app.config['ethereum'] and app.config['ethereum']['url'] is not None:
        if 'gcm' in app.config and 'server_key' in app.config['gcm'] and app.config['gcm']['server_key'] is not None:
            pushclient = GCMHttpPushClient(app.config['gcm']['server_key'])
        elif 'pushserver' in app.config and 'url' in app.config['pushserver'] and app.config['pushserver']['url'] is not None:
            pushclient = PushServerClient(url=app.config['pushserver']['url'],
                                          username=app.config['pushserver'].get('username'),
                                          password=app.config['pushserver'].get('password'))
        else:
            pushclient = None
        app.monitor = BlockMonitor(app.connection_pool, app.config['ethereum']['url'], pushclient=pushclient)
        app.monitor.start()
    app.start()
