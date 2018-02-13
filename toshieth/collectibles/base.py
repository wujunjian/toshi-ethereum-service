import os
from toshi.tasks import TaskHandler
from toshieth.tasks import TaskListenerApplication
from toshi.jsonrpc.client import JsonRPCClient

class CollectiblesProcessingHandler(TaskHandler):

    async def process_block(self, block_number=None):
        self.listener.application.ioloop.add_callback(self.listener.application.process_block)

class CollectiblesTaskManager(TaskListenerApplication):

    def __init__(self, *args, **kwargs):
        super().__init__([(CollectiblesProcessingHandler,)], *args,
                         listener_id=self.__class__.__name__, **kwargs)
        self.eth = JsonRPCClient(self.config['ethereum']['url'], should_retry=False)
        self.ioloop.add_callback(self.process_block)

    def process_config(self):
        config = super().process_config()
        if 'COLLECTIBLE_IMAGE_FORMAT_STRING' in os.environ:
            config['collectibles'] = {'image_format': os.environ['COLLECTIBLE_IMAGE_FORMAT_STRING']}
        else:
            raise Exception("Missing $COLLECTIBLE_IMAGE_FORMAT_STRING")
        return config

    async def process_block(self):
        raise NotImplementedError()
