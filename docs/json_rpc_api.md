# SmellyCoin JSON-RPC API Documentation

This document describes the JSON-RPC API provided by SmellyCoin nodes. The API allows developers to interact with the SmellyCoin blockchain, query data, submit transactions, and perform mining operations.

## Table of Contents

- [Overview](#overview)
- [Authentication](#authentication)
- [Request Format](#request-format)
- [Response Format](#response-format)
- [Error Codes](#error-codes)
- [API Methods](#api-methods)
  - [Blockchain Methods](#blockchain-methods)
  - [Network Methods](#network-methods)
  - [Transaction Methods](#transaction-methods)
  - [Mining Methods](#mining-methods)
  - [Utility Methods](#utility-methods)

## Overview

The SmellyCoin JSON-RPC API follows the [JSON-RPC 2.0 specification](https://www.jsonrpc.org/specification). It is accessible via HTTP POST requests to the RPC endpoint, which is typically `http://localhost:8332/` for mainnet nodes.

## Authentication

Access to the API requires HTTP Basic Authentication using the `rpcuser` and `rpcpassword` values specified in your `smellycoin.conf` file. For security reasons, it's recommended to use strong, unique credentials and restrict RPC access to trusted IP addresses using the `rpcallowip` configuration option.

## Request Format

All requests must be sent as HTTP POST with a JSON payload. The payload must include:

- `jsonrpc`: Must be "2.0"
- `method`: The name of the method to call
- `params`: An array or object containing the parameters for the method
- `id`: A unique identifier for the request

Example request:

```json
{
  "jsonrpc": "2.0",
  "method": "getblockcount",
  "params": [],
  "id": 1
}
```

## Response Format

Responses are JSON objects containing:

- `jsonrpc`: Always "2.0"
- `result`: The result of the method call (if successful)
- `error`: Error information (if an error occurred)
- `id`: The same id that was sent in the request

Example successful response:

```json
{
  "jsonrpc": "2.0",
  "result": 123456,
  "id": 1
}
```

Example error response:

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32601,
    "message": "Method not found"
  },
  "id": 1
}
```

## Error Codes

The API uses the following error codes:

| Code     | Message            | Description                                  |
|----------|-------------------|----------------------------------------------|
| -32700   | Parse error        | Invalid JSON was received                    |
| -32600   | Invalid request    | The JSON sent is not a valid Request object  |
| -32601   | Method not found   | The method does not exist                    |
| -32602   | Invalid params     | Invalid method parameters                    |
| -32603   | Internal error     | Internal JSON-RPC error                      |
| -1       | Invalid address    | The provided address is invalid              |
| -2       | TX validation error| Transaction validation failed                |
| -3       | Block validation error| Block validation failed                   |
| -4       | Not found          | Requested data was not found                 |
| -28      | RPC in warmup      | The RPC server is still initializing         |

## API Methods

### Blockchain Methods

#### `getbestblockhash`

Returns the hash of the best (tip) block in the longest blockchain.

**Parameters**: None

**Result**: The block hash (string)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getbestblockhash","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getblock`

Returns information about a block.

**Parameters**:
1. `blockhash` (string, required): The block hash
2. `verbose` (boolean, optional, default=false): True for a detailed JSON object, false for the serialized block

**Result (verbose=true)**: A JSON object with block information

**Result (verbose=false)**: Serialized block data (string)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblock","params":["000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f", true]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getblockchaininfo`

Returns information about the current state of the blockchain.

**Parameters**: None

**Result**: A JSON object with blockchain information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblockchaininfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getblockcount`

Returns the height of the most-work fully-validated chain.

**Parameters**: None

**Result**: The current block count (number)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblockcount","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getblockhash`

Returns the hash of a block at the given height in the longest blockchain.

**Parameters**:
1. `height` (number, required): The height of the block

**Result**: The block hash (string)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblockhash","params":[1000]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `gettxout`

Returns details about an unspent transaction output (UTXO).

**Parameters**:
1. `txid` (string, required): The transaction id
2. `n` (number, required): The output number (vout)
3. `include_mempool` (boolean, optional, default=true): Whether to include the mempool

**Result**: A JSON object with UTXO information, or null if the output is spent or doesn't exist

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"gettxout","params":["txid", 0, true]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

### Network Methods

#### `getconnectioncount`

Returns the number of connections to other nodes.

**Parameters**: None

**Result**: The connection count (number)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getconnectioncount","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getpeerinfo`

Returns data about each connected network node.

**Parameters**: None

**Result**: An array of objects with peer information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getpeerinfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getnetworkinfo`

Returns information about the node's network connection.

**Parameters**: None

**Result**: A JSON object with network information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getnetworkinfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

### Transaction Methods

#### `sendrawtransaction`

Submits a raw transaction to the network.

**Parameters**:
1. `hexstring` (string, required): The hex-encoded transaction data

**Result**: The transaction hash (string)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"sendrawtransaction","params":["hexstring"]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getrawtransaction`

Returns the raw transaction data.

**Parameters**:
1. `txid` (string, required): The transaction id
2. `verbose` (boolean, optional, default=false): If true, returns a JSON object, otherwise the hex-encoded data

**Result (verbose=true)**: A JSON object with transaction information

**Result (verbose=false)**: The serialized transaction (string)

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getrawtransaction","params":["txid", true]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `decoderawtransaction`

Decodes a raw transaction and returns a JSON object representing it.

**Parameters**:
1. `hexstring` (string, required): The hex-encoded transaction data

**Result**: A JSON object with transaction information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"decoderawtransaction","params":["hexstring"]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

### Mining Methods

#### `getmininginfo`

Returns mining-related information.

**Parameters**: None

**Result**: A JSON object with mining information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getmininginfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `submitblock`

Submits a new block to the network.

**Parameters**:
1. `hexdata` (string, required): The hex-encoded block data

**Result**: null if successful, or an error string

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"submitblock","params":["hexdata"]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

#### `getblocktemplate`

Returns data needed to construct a block.

**Parameters**:
1. `template_request` (object, optional): A JSON object with template request parameters

**Result**: A JSON object with block template information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblocktemplate","params":[{"capabilities": ["coinbasetxn", "workid", "coinbase/append"]}]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

### Utility Methods

#### `validateaddress`

Checks if an address is valid.

**Parameters**:
1. `address` (string, required): The SmellyCoin address to validate

**Result**: A JSON object with validation information

**Example**:
```bash
curl --user username:password --data-binary '{"jsonrpc":"2.0","id":"1","method":"validateaddress","params":["address"]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

## Using the API with Programming Languages

### Python Example

```python
import requests
import json

url = "http://localhost:8332"
headers = {"content-type": "application/json"}
payload = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "getblockcount",
    "params": []
}
response = requests.post(
    url,
    data=json.dumps(payload),
    headers=headers,
    auth=("username", "password")
)
result = response.json()
print(result)
```

### JavaScript Example

```javascript
const fetch = require('node-fetch');

const url = 'http://localhost:8332';
const username = 'username';
const password = 'password';
const headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Basic ' + Buffer.from(`${username}:${password}`).toString('base64')
};

const payload = {
  jsonrpc: '2.0',
  id: '1',
  method: 'getblockcount',
  params: []
};

fetch(url, {
  method: 'POST',
  headers: headers,
  body: JSON.stringify(payload)
})
.then(response => response.json())
.then(data => console.log(data))
.catch(error => console.error('Error:', error));
```

## Security Considerations

1. **Access Control**: Always restrict RPC access to trusted IP addresses using `rpcallowip`.
2. **Strong Credentials**: Use strong, unique credentials for RPC authentication.
3. **HTTPS**: Consider using HTTPS for RPC connections in production environments.
4. **Firewall**: Configure your firewall to restrict access to the RPC port.
5. **Least Privilege**: Create separate RPC users with limited permissions when possible.

## Rate Limiting

The SmellyCoin node implements rate limiting to prevent abuse. Excessive requests may be temporarily blocked. The default limit is 200 requests per minute.

## API Versioning

The API follows semantic versioning. Breaking changes will only be introduced in major version updates. The current API version can be obtained from the `getnetworkinfo` method.