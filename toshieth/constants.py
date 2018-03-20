from toshi.ethereum.utils import sha3

TRANSFER_TOPIC = '0x' + sha3("Transfer(address,address,uint256)").hex()
DEPOSIT_TOPIC = '0x' + sha3("Deposit(address,uint256)").hex()
WITHDRAWAL_TOPIC = '0x' + sha3("Withdrawal(address,uint256)").hex()

WETH_CONTRACT_ADDRESS = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
