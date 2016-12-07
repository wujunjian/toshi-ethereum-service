from . import handlers
from asyncbb.web import Application

urls = [
    (r"^/v1/tx/skel/?$", handlers.TransactionSkeletonHandler),
    (r"^/v1/tx/?$", handlers.SendTransactionHandler),
    (r"^/v1/tx/(0x[0-9a-fA-F]{64})/?$", handlers.TransactionHandler)
]

def main():

    app = Application(urls)
    app.start()
