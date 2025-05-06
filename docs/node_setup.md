# SmellyCoin Node Setup Guide

This guide provides instructions for setting up a SmellyCoin node, either as a genesis node to start a new blockchain or as a regular node to join an existing network.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Setting Up a Genesis Node](#setting-up-a-genesis-node)
- [Joining an Existing Network](#joining-an-existing-network)
- [Node Operation](#node-operation)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Before setting up a SmellyCoin node, ensure you have the following:

- A computer with at least 2GB RAM and 20GB free disk space
- Rust programming language (1.70.0 or later)
- Git
- Internet connection (for syncing with the network)
- Basic knowledge of command-line operations

## Installation

### Building from Source

1. Clone the SmellyCoin repository:

```bash
git clone https://github.com/smellycoin/smellycoinr.git
cd smellycoinr
```

2. Build the project:

```bash
cargo build --release
```

3. The compiled binary will be available at `target/release/smellycoin`

## Configuration

SmellyCoin uses a configuration file to customize node behavior. A sample configuration file is provided at `config/smellycoin.conf.example`.

1. Create a data directory for SmellyCoin:

```bash
mkdir -p ~/.smellycoin
```

2. Copy the example configuration file:

```bash
cp config/smellycoin.conf.example ~/.smellycoin/smellycoin.conf
```

3. Edit the configuration file to suit your needs:

```bash
nano ~/.smellycoin/smellycoin.conf
```

Refer to the comments in the configuration file for details on each option.

## Setting Up a Genesis Node

A genesis node is the first node in a new blockchain network. It creates and mines the genesis block, which is the foundation of the entire blockchain.

### Step 1: Configure for Genesis Node

Edit your `smellycoin.conf` file and set the following parameters:

```
# Network Configuration
network=mainnet  # or testnet or regtest
listen=1
port=8333

# Mining Configuration
mine=1
miningaddress=YOUR_SMELLYCOIN_ADDRESS  # Replace with your address

# RPC Configuration
rpc=1
rpcbind=127.0.0.1:8332
rpcuser=YOUR_USERNAME  # Choose a secure username
rpcpassword=YOUR_PASSWORD  # Choose a secure password
```

### Step 2: Initialize the Blockchain

Run the initialization command to create the genesis block:

```bash
./target/release/smellycoin init --force
```

This command creates the genesis block according to the network parameters specified in your configuration file.

### Step 3: Start the Genesis Node

Start the node with mining enabled:

```bash
./target/release/smellycoin --mine --mining-address=YOUR_SMELLYCOIN_ADDRESS
```

The node will start mining blocks, beginning with the genesis block.

### Step 4: Verify Genesis Block Creation

You can verify that the genesis block was created successfully using the JSON-RPC API:

```bash
curl --user YOUR_USERNAME:YOUR_PASSWORD --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblock","params":["0"]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

## Joining an Existing Network

To join an existing SmellyCoin network, follow these steps:

### Step 1: Configure for Network Joining

Edit your `smellycoin.conf` file and set the following parameters:

```
# Network Configuration
network=mainnet  # Must match the network you're joining
listen=1
port=8333

# Add seed nodes to connect to the network
addnode=seed1.smellycoin.org:8333  # Replace with actual seed nodes
addnode=seed2.smellycoin.org:8333

# RPC Configuration (optional)
rpc=1
rpcbind=127.0.0.1:8332
rpcuser=YOUR_USERNAME
rpcpassword=YOUR_PASSWORD
```

### Step 2: Start the Node

Start the node without mining:

```bash
./target/release/smellycoin
```

### Step 3: Monitor Synchronization

The node will automatically begin synchronizing with the network. You can monitor the progress using the JSON-RPC API:

```bash
curl --user YOUR_USERNAME:YOUR_PASSWORD --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblockchaininfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

Look for the `verificationprogress` field in the response, which indicates the synchronization progress (0.0 to 1.0).

## Node Operation

### Starting and Stopping

To start the node:

```bash
./target/release/smellycoin
```

To stop the node, press `Ctrl+C` in the terminal where it's running.

### Checking Node Status

Use the following JSON-RPC commands to check the status of your node:

- Get blockchain information:

```bash
curl --user YOUR_USERNAME:YOUR_PASSWORD --data-binary '{"jsonrpc":"2.0","id":"1","method":"getblockchaininfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

- Get network information:

```bash
curl --user YOUR_USERNAME:YOUR_PASSWORD --data-binary '{"jsonrpc":"2.0","id":"1","method":"getnetworkinfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

- Get peer information:

```bash
curl --user YOUR_USERNAME:YOUR_PASSWORD --data-binary '{"jsonrpc":"2.0","id":"1","method":"getpeerinfo","params":[]}' -H 'content-type: application/json' http://127.0.0.1:8332/
```

### Backup and Recovery

It's important to regularly back up your node's data directory, especially the wallet files if you're using the node for mining or transactions.

To back up the data directory:

```bash
cp -r ~/.smellycoin ~/.smellycoin.backup
```

To restore from a backup:

```bash
rm -rf ~/.smellycoin
cp -r ~/.smellycoin.backup ~/.smellycoin
```

## Troubleshooting

### Common Issues

1. **Node won't start**
   - Check if another instance is already running
   - Verify the configuration file syntax
   - Ensure the data directory is writable

2. **Node won't connect to the network**
   - Check your internet connection
   - Verify that the port is open in your firewall
   - Try adding more seed nodes in the configuration file

3. **Synchronization is slow or stalled**
   - Check your internet connection speed
   - Increase the `dbcache` value in the configuration file
   - Try connecting to different seed nodes

4. **Mining is not working**
   - Verify that your mining address is valid
   - Check the mining logs for errors
   - Ensure your hardware meets the minimum requirements

### Logs

Check the log file for detailed error messages:

```bash
tail -f ~/.smellycoin/debug.log
```

### Getting Help

If you encounter issues not covered in this guide, you can seek help through:

- The SmellyCoin GitHub repository issues section
- Community forums and chat channels
- The official SmellyCoin documentation website