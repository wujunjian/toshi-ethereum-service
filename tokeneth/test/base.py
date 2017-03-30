import asyncio

import tokeneth.monitor

from tokenservices.test.base import AsyncHandlerTest
from tokeneth.app import urls
from tornado.escape import json_decode

from tokenservices.ethereum.utils import private_key_to_address, data_encoder
from tokenservices.ethereum.tx import sign_transaction

class EthServiceBaseTest(AsyncHandlerTest):

    def get_urls(self):
        return urls

    def get_url(self, path):
        path = "/v1{}".format(path)
        return super().get_url(path)

    async def wait_on_tx_confirmation(self, tx_hash, interval_check_callback=None):
        while True:
            resp = await self.fetch("/tx/{}".format(tx_hash))
            self.assertEqual(resp.code, 200)
            body = json_decode(resp.body)
            if body is None or body['blockNumber'] is None:
                if interval_check_callback:
                    f = interval_check_callback()
                    if asyncio.iscoroutine(f):
                        await f
                await asyncio.sleep(1)
            else:
                return body

    async def get_tx_skel(self, from_key, to_addr, val, nonce=None):
        from_addr = private_key_to_address(from_key)
        body = {
            "from": from_addr,
            "to": to_addr,
            "value": val
        }
        if nonce is not None:
            body['nonce'] = nonce

        resp = await self.fetch("/tx/skel", method="POST", body=body)

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)

        tx = body['tx']

        return tx

    async def sign_and_send_tx(self, from_key, tx):

        tx = sign_transaction(tx, from_key)

        body = {
            "tx": tx
        }

        resp = await self.fetch("/tx", method="POST", body=body)

        self.assertResponseCodeEqual(resp, 200, resp.body)

        body = json_decode(resp.body)
        tx_hash = body['tx_hash']
        return tx_hash

    async def send_tx(self, from_key, to_addr, val, nonce=None):

        tx = await self.get_tx_skel(from_key, to_addr, val, nonce=nonce)
        return await self.sign_and_send_tx(from_key, tx)


def requires_block_monitor(func=None, cls=tokeneth.monitor.BlockMonitor, pass_monitor=False, begin_started=True):
    """Used to ensure all database connections are returned to the pool
    before finishing the test"""

    def wrap(fn):

        async def wrapper(self, *args, **kwargs):

            if 'ethereum' not in self._app.config:
                raise Exception("Missing ethereum config from setup")

            self._app.monitor = cls(self._app.connection_pool, self._app.config['ethereum']['url'])

            if begin_started:
                await self._app.monitor.start()

            if pass_monitor:
                if pass_monitor is True:
                    kwargs['monitor'] = self._app.monitor
                else:
                    kwargs[pass_monitor] = self._app.monitor

            try:
                f = fn(self, *args, **kwargs)
                if asyncio.iscoroutine(f):
                    await f
            finally:
                await self._app.monitor.shutdown()

        return wrapper

    if func is not None:
        return wrap(func)
    else:
        return wrap
