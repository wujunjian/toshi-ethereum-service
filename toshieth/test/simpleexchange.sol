pragma solidity ^0.4.19;

contract Token {

  function totalSupply() constant returns (uint supply) {}
  function balanceOf(address _owner) constant returns (uint balance) {}
  function transfer(address _to, uint _value) returns (bool success) {}
  function transferFrom(address _from, address _to, uint _value) returns (bool success) {}
  function approve(address _spender, uint _value) returns (bool success) {}
  function allowance(address _owner, address _spender) constant returns (uint remaining) {}

  event Transfer(address indexed _from, address indexed _to, uint _value);
  event Approval(address indexed _owner, address indexed _spender, uint _value);
}

contract SimpleExchange {

  address public ZRX_CONTRACT;
  uint256 public ZRX_COST = 10 ** 18;

  struct Order {
    address _src;
    address _dst;
    uint256 _src_val;
    uint256 _dst_val;
  }

  mapping (address => Order) orders;

  function SimpleExchange(address _zrx_contract) {
    ZRX_CONTRACT = _zrx_contract;
  }

  function createOrder(address _src, uint256 _src_val, address _dst, uint256 _dst_val) public {
    require(_src != 0);
    require(_dst != 0);
    orders[msg.sender] = Order(_src, _dst, _src_val, _dst_val);
  }

  function fillOrder(address _orderer) public {
    require(orders[_orderer]._src != 0);
    Order storage order = orders[_orderer];
    Token srcToken = Token(order._src);
    Token dstToken = Token(order._dst);
    Token zrxToken = Token(ZRX_CONTRACT);
    require(srcToken.balanceOf(_orderer) >= order._src_val);
    require(dstToken.balanceOf(msg.sender) >= order._dst_val);
    require(zrxToken.balanceOf(_orderer) >= ZRX_COST);
    require(zrxToken.balanceOf(msg.sender) >= ZRX_COST);
    require(zrxToken.allowance(msg.sender, this) >= ZRX_COST);
    require(zrxToken.allowance(_orderer, this) >= ZRX_COST);
    require(srcToken.allowance(msg.sender, this) >= order._src_val);
    require(dstToken.allowance(_orderer, this) >= order._dst_val);

    srcToken.transferFrom(_orderer, msg.sender, order._src_val);
    dstToken.transferFrom(msg.sender, _orderer, order._dst_val);
    zrxToken.transferFrom(msg.sender, this, ZRX_COST);
    zrxToken.transferFrom(_orderer, this, ZRX_COST);
    delete orders[_orderer];
  }
}
