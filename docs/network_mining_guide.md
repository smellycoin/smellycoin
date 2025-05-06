# SmellyCoin Network and Mining Guide

## Table of Contents

1. [Introduction](#introduction)
2. [Network Setup](#network-setup)
   - [Joining the Main Network](#joining-the-main-network)
   - [Setting Up a Test Network](#setting-up-a-test-network)
   - [Network Configuration](#network-configuration)
3. [Mining](#mining)
   - [Solo Mining](#solo-mining)
   - [Pool Mining](#pool-mining)
   - [Mining with Block Headers](#mining-with-block-headers)
4. [Mining Pool Setup](#mining-pool-setup)
   - [Pool Configuration](#pool-configuration)
   - [Stratum Server Setup](#stratum-server-setup)
   - [Reward Distribution](#reward-distribution)
5. [Web-Based Mining with WASM](#web-based-mining-with-wasm)
   - [Setting Up WASM Mining](#setting-up-wasm-mining)
   - [Mobile Mining Integration](#mobile-mining-integration)
6. [Web Wallet Integration](#web-wallet-integration)

## Introduction

SmellyCoin is a cryptocurrency that uses the KAWPOW algorithm for mining. This guide provides comprehensive instructions for setting up nodes, mining individually or in pools, and integrating with web-based services.

## Network Setup

### Joining the Main Network

To join the SmellyCoin main network:

1. Install the SmellyCoin node software:
   ```bash
   git clone https://github.com/smellycoin/smellycoin.git
   cd smellycoin
   cargo build --release
   ```

2. Create a configuration file:
   ```bash
   mkdir -p ~/.smellycoin
   cp config/smellycoin.conf.example ~/.smellycoin/smellycoin.conf
   ```

3. Edit the configuration file to set up your node:
   ```
   # Network settings
   network=mainnet
   listen=1
   port=8333
   maxconnections=125
   
   # RPC settings
   rpcuser=your_username
   rpcpassword=your_secure_password
   rpcport=8332
   rpcallowip=127.0.0.1
   ```

4. Start the node:
   ```bash
   ./target/release/smellycoin
   ```

### Setting Up a Test Network

For development and testing purposes, you can set up a test network:

1. Modify your configuration file:
   ```
   network=testnet
   listen=1
   port=18333
   testnet=1
   
   rpcuser=your_username
   rpcpassword=your_secure_password
   rpcport=18332
   rpcallowip=127.0.0.1
   ```

2. Start the node in testnet mode:
   ```bash
   ./target/release/smellycoin --testnet
   ```

### Network Configuration

The SmellyCoin network can be configured through the `smellycoin.conf` file or command-line arguments:

- `network`: Network type (mainnet, testnet, regtest)
- `listen`: Enable incoming connections (0 or 1)
- `port`: Network port for P2P connections
- `maxconnections`: Maximum number of connections
- `addnode`: Add a node to connect to
- `connect`: Connect only to the specified node(s)
- `dnsseed`: Use DNS seeds for peer discovery (0 or 1)
- `seednode`: Connect to a seed node for peer discovery

## Mining

### Solo Mining

Solo mining allows you to mine blocks directly without joining a pool:

1. Configure your node for mining:
   ```
   # Mining settings
   gen=1
   miningaddress=your_smellycoin_address
   ```

2. Start the CPU miner:
   ```bash
   ./target/release/smellycoin --mine
   ```

3. For GPU mining, you can use external miners that support KAWPOW and connect them to your node via the RPC interface.

### Pool Mining

To mine with a pool:

1. Choose a SmellyCoin mining pool
2. Download a compatible miner (e.g., T-Rex, TeamRedMiner, or NBMiner)
3. Configure your miner to connect to the pool:

   For T-Rex:
   ```bash
   t-rex -a kawpow -o stratum+tcp://pool_address:port -u your_wallet_address -p x
   ```

   For TeamRedMiner:
   ```bash
   teamredminer -a kawpow -o stratum+tcp://pool_address:port -u your_wallet_address -p x
   ```

### Mining with Block Headers

SmellyCoin supports mining with block headers only, which reduces bandwidth requirements and allows for more efficient mining:

1. The mining protocol transmits only block headers instead of full blocks
2. Miners validate and mine based on these headers
3. This approach is particularly useful for mobile and web-based mining

## Mining Pool Setup

### Pool Configuration

To set up your own SmellyCoin mining pool:

1. Install the SmellyCoin node and pool software:
   ```bash
   git clone https://github.com/smellycoin/smellycoin.git
   cd smellycoin
   cargo build --release --features="mining-pool"
   ```

2. Create a pool configuration file:
   ```bash
   mkdir -p ~/.smellycoin/pool
   cp config/pool.conf.example ~/.smellycoin/pool/pool.conf
   ```

3. Configure your pool settings:
   ```
   # Pool settings
   fee_percent=1.0
   min_payment_threshold=100000000
   operator_address=your_smellycoin_address
   stratum_bind_addr=0.0.0.0:3333
   job_refresh_interval=30
   share_difficulty=0.1
   ```

4. Start the pool server:
   ```bash
   ./target/release/smellycoin --pool
   ```

### Stratum Server Setup

The Stratum server handles connections from miners:

1. The server listens on the configured address (default: 0.0.0.0:3333)
2. It implements the Stratum protocol v1 with extensions for KAWPOW
3. Miners connect using compatible mining software
4. The server distributes work, validates shares, and tracks miner statistics

### Reward Distribution

The pool distributes rewards based on shares submitted by miners:

1. When a block is found, the reward is split among miners based on their contribution
2. The pool takes a fee (configured by `fee_percent`)
3. Miners receive payments when their balance exceeds the minimum threshold
4. The distribution follows a PPLNS (Pay Per Last N Shares) model by default

## Web-Based Mining with WASM

### Setting Up WASM Mining

SmellyCoin supports web-based mining using WebAssembly (WASM):

1. Include the SmellyCoin WASM mining library in your web application:
   ```html
   <script src="/js/smellycoin-miner.js"></script>
   ```

2. Initialize the miner:
   ```javascript
   const miner = new SmellyCoinMiner({
     pool: 'stratum+tcp://pool_address:port',
     address: 'your_wallet_address',
     threads: 4, // Number of threads to use
     throttle: 0.5 // Throttle factor (0-1)
   });
   ```

3. Start and stop mining:
   ```javascript
   // Start mining
   miner.start();
   
   // Stop mining
   miner.stop();
   ```

### Mobile Mining Integration

For mobile web-based mining:

1. Optimize the WASM miner for mobile devices by adjusting thread count and throttle
2. Implement battery and temperature monitoring to prevent overheating
3. Use service workers for background mining when supported
4. Consider a progressive web app (PWA) for improved performance

## Web Wallet Integration

SmellyCoin can be integrated with web wallets:

1. Use the SmellyCoin JavaScript library for wallet functionality:
   ```javascript
   import { SmellyCoinWallet } from 'smellycoin-js';
   
   const wallet = new SmellyCoinWallet();
   const address = wallet.generateAddress();
   ```

2. Connect to a node for blockchain data:
   ```javascript
   wallet.connect('https://node_address:port');
   ```

3. Implement key wallet functions:
   ```javascript
   // Get balance
   const balance = await wallet.getBalance();
   
   // Send transaction
   const txid = await wallet.sendTransaction(recipientAddress, amount);
   
   // View transaction history
   const history = await wallet.getTransactionHistory();
   ```

4. Secure the wallet with encryption and backup options