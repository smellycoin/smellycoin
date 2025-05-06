# SmellyCoin Setup Guide for Linux and macOS

This guide provides step-by-step instructions for setting up a SmellyCoin node on Linux and macOS, including installation, configuration, wallet creation, and mining.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Linux Installation](#linux-installation)
  - [macOS Installation](#macos-installation)
- [Configuration](#configuration)
- [Running a Node](#running-a-node)
- [Creating a Wallet](#creating-a-wallet)
- [Mining](#mining)
- [Using the JSON-RPC API](#using-the-json-rpc-api)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Before setting up SmellyCoin, ensure you have the following:

- Linux: Ubuntu 20.04+ / Debian 11+ / Fedora 35+ or equivalent
- macOS: macOS 11 (Big Sur) or later
- At least 4GB RAM (8GB recommended)
- At least 50GB free disk space
- Internet connection

## Installation

### Linux Installation

#### Installing Dependencies

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install -y build-essential git curl pkg-config libssl-dev
```

**Fedora:**

```bash
sudo dnf install -y gcc gcc-c++ make git curl pkgconfig openssl-devel
```

#### Installing Rust

1. Install Rust using rustup:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

2. Follow the on-screen instructions to complete the installation
3. Load Rust environment variables:

```bash
source $HOME/.cargo/env
```

4. Verify the installation:

```bash
rustc --version
cargo --version
```

#### Building SmellyCoin

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

### macOS Installation

#### Installing Dependencies

1. Install Homebrew if not already installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

2. Install required dependencies:

```bash
brew install openssl pkg-config
```

#### Installing Rust

1. Install Rust using rustup:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

2. Follow the on-screen instructions to complete the installation
3. Load Rust environment variables:

```bash
source $HOME/.cargo/env
```

4. Verify the installation:

```bash
rustc --version
cargo --version
```

#### Building SmellyCoin

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

1. Create a data directory for SmellyCoin:

```bash
mkdir -p ~/.smellycoin
```

2. Copy the example configuration file:

```bash
cp config/smellycoin.conf.example ~/.smellycoin/smellycoin.conf
```

3. Edit the configuration file using your preferred text editor:

```bash
nano ~/.smellycoin/smellycoin.conf
```

4. Configure the following settings:

```
# Network Configuration
network=mainnet  # or testnet or regtest
listen=1
port=8333

# RPC Configuration
rpc=1
rpcbind=127.0.0.1:8332
rpcuser=YOUR_USERNAME  # Choose a secure username
rpcpassword=YOUR_PASSWORD  # Choose a secure password

# Data Directory
datadir=~/.smellycoin
```

## Running a Node

### Joining the Main Network

1. Navigate to the SmellyCoin directory
2. Run the node:

```bash
./target/release/smellycoin --config=~/.smellycoin/smellycoin.conf
```

### Creating a New Blockchain

To initialize a new blockchain (genesis node):

1. Configure your `smellycoin.conf` file as shown above
2. Run the initialization command:

```bash
./target/release/smellycoin init --force --config=~/.smellycoin/smellycoin.conf
```

3. Start the node:

```bash
./target/release/smellycoin --config=~/.smellycoin/smellycoin.conf
```

### Running as a Service (Linux only)

1. Create a systemd service file:

```bash
sudo nano /etc/systemd/system/smellycoin.service
```

2. Add the following content (adjust paths as needed):

```
[Unit]
Description=SmellyCoin Node
After=network.target

[Service]
User=YOUR_USERNAME
Group=YOUR_GROUP
Type=simple
ExecStart=/path/to/smellycoinr/target/release/smellycoin --config=/home/YOUR_USERNAME/.smellycoin/smellycoin.conf
Restart=on-failure
RestartSec=30
TimeoutSec=240
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

3. Enable and start the service:

```bash
sudo systemctl enable smellycoin
sudo systemctl start smellycoin
```

4. Check the status:

```bash
sudo systemctl status smellycoin
```

## Creating a Wallet

1. With your node running, open a new terminal
2. Use the RPC client to create a new wallet:

```bash
./target/release/smellycoin createwallet "mywallet" --config=~/.smellycoin/smellycoin.conf
```

3. Generate a new address:

```bash
./target/release/smellycoin getnewaddress --config=~/.smellycoin/smellycoin.conf
```

4. Save your address for receiving coins

## Mining

### CPU Mining

1. Edit your configuration file to enable mining:

```
# Mining Configuration
mine=1
miningaddress=YOUR_SMELLYCOIN_ADDRESS  # Replace with your address
threads=4  # Number of CPU threads to use
```

2. Restart your node to apply the changes

### GPU Mining (using external miner)

1. Ensure your node is running with RPC enabled
2. Download a compatible KAWPOW miner (such as T-Rex, TeamRedMiner, or NBMiner)

**Linux:**

```bash
# Example for T-Rex miner
chmod +x t-rex
./t-rex -a kawpow -o stratum+tcp://127.0.0.1:3333 -u YOUR_SMELLYCOIN_ADDRESS -p x
```

**macOS:**
Note: GPU mining support on macOS is limited. CPU mining is recommended.

### Setting Up a Mining Pool

1. Edit your configuration file to enable the Stratum server:

```
# Stratum Server Configuration
stratum=1
stratumbind=0.0.0.0:3333
```

2. Restart your node to apply the changes
3. Miners can now connect to your pool using your IP address and port 3333

## Using the JSON-RPC API

### Using curl

```bash
curl --user "YOUR_USERNAME:YOUR_PASSWORD" --data-binary '{"jsonrpc":"1.0","id":"curltest","method":"getblockchaininfo","params":[]}' -H "content-type: text/plain" http://127.0.0.1:8332/
```

### Using Python

```python
import requests
import json

url = "http://127.0.0.1:8332/"
headers = {"content-type": "text/plain"}
payload = {
    "jsonrpc": "1.0",
    "id": "pythontest",
    "method": "getblockchaininfo",
    "params": []
}
auth = ("YOUR_USERNAME", "YOUR_PASSWORD")

response = requests.post(url, data=json.dumps(payload), headers=headers, auth=auth)
print(json.dumps(response.json(), indent=4))
```

## Troubleshooting

### Node Won't Start

- Check if another process is using the configured ports:
  ```bash
lsof -i :8333
  ```
- Verify your configuration file syntax
- Check firewall settings:
  ```bash
sudo ufw status
  ```
- Examine the log file at `~/.smellycoin/debug.log`

### Synchronization Issues

- Ensure your internet connection is stable
- Check if your firewall is blocking connections
- Try adding known peers in your configuration file:
  ```
addnode=NODE_IP:8333
  ```

### Mining Problems

- Verify your mining address is correct
- Check system resources (CPU/GPU usage, temperature):
  ```bash
htop
  ```
- For GPU mining, ensure you have the latest drivers installed

### RPC Connection Issues

- Verify RPC username and password
- Check if the RPC server is running and bound to the correct address:
  ```bash
lsof -i :8332
  ```
- Ensure no firewall is blocking the RPC port

## Additional Resources

- [SmellyCoin GitHub Repository](https://github.com/smellycoin/smellycoinr)
- [SmellyCoin Discord Community](https://discord.gg/smellycoin)
- [SmellyCoin Documentation](https://docs.smellycoin.org)