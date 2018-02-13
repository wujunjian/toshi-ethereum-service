if __name__ == "__main__":
    import asyncio
    from toshieth.collectibles import punks
    from toshieth.collectibles import erc721

    erc721.ERC721TaskManager().start()
    punks.CryptoPunksTaskManager().start()
    asyncio.get_event_loop().run_forever()
