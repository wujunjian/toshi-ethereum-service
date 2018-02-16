pragma solidity ^0.4.20;

contract Splitter {

  address[] addresses;

  event Payment(address _from);
  event Withdrawal(address _by);

  function Splitter(address[] _addresses) {
    addresses = _addresses;
  }

  function withdraw() public {
    require(this.balance > 0);
    uint value = this.balance / addresses.length;
    for (uint i = 0; i < addresses.length; i++) {
      addresses[i].send(value);
    }
    Withdrawal(msg.sender);
  }

  function() public payable {
    if (msg.value == 0) return;
    Payment(msg.sender);
  }
}
