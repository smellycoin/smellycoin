# SmellyCoin Setup Guide for Windows

This guide provides step-by-step instructions for setting up a SmellyCoin node on Windows, including installation, configuration, wallet creation, and mining.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running a Node](#running-a-node)
- [Creating a Wallet](#creating-a-wallet)
- [Mining](#mining)
- [Using the JSON-RPC API](#using-the-json-rpc-api)
- [Troubleshooting](#troubleshooting)

## Prerequisites

Before setting up SmellyCoin on Windows, ensure you have the following:

- Windows 10 or later (64-bit recommended)
- At least 4GB RAM (8GB recommended)
- At least 50GB free disk space
- Internet connection
- Administrator privileges

## Installation

### Installing Rust

1. Download and run the Rust installer from [rustup.rs](https://rustup.rs/)
2. Follow the on-screen instructions to complete the installation
3. Open a new Command Prompt and verify the installation:

```cmd
rustc --version
cargo --version
```

### Installing Git

1. Download Git for Windows from [git-scm.com](https://git-scm.com/download/win)
2. Run the installer and follow the on-screen instructions
3. Verify the installation:

```cmd
git --version
```

### Building SmellyCoin

1. Open Command Prompt as Administrator
2. Clone the SmellyCoin repository:

```cmd
git clone https://github.com/smellycoin/smellycoinr.git
cd smellycoinr
```

3. Build the project:

```cmd
cargo build --release
```

4. The compiled binary will be available at `target\release\smellycoin.exe`

## Configuration

1. Create a data directory for SmellyCoin:

```cmd
mkdir %USERPROFILE%\.smellycoin
```

2. Copy the example configuration file:

```cmd
copy config\smellycoin.conf.example %USERPROFILE%\.smellycoin\smellycoin.conf
```

3. Edit the configuration file using Notepad or another text editor:

```cmd
notepad %USERPROFILE%\.smellycoin\smellycoin.conf
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
datadir=%USERPROFILE%\.smellycoin
```

## Running a Node

### Joining the Main Network

1. Open Command Prompt as Administrator
2. Navigate to the SmellyCoin directory
3. Run the node:

```cmd
target\release\smellycoin.exe --config=%USERPROFILE%\.smellycoin\smellycoin.conf
```

### Creating a New Blockchain

To initialize a new blockchain (genesis node):

1. Configure your `smellycoin.conf` file as shown above
2. Run the initialization command:

```cmd
target\release\smellycoin.exe init --force --config=%USERPROFILE%\.smellycoin\smellycoin.conf
```

3. Start the node:

```cmd
target\release\smellycoin.exe --config=%USERPROFILE%\.smellycoin\smellycoin.conf
```

## Creating a Wallet

1. With your node running, open a new Command Prompt
2. Use the RPC client to create a new wallet:

```cmd
target\release\smellycoin.exe createwallet "mywallet" --config=%USERPROFILE%\.smellycoin\smellycoin.conf
```

3. Generate a new address:

```cmd
target\release\smellycoin.exe getnewaddress --config=%USERPROFILE%\.smellycoin\smellycoin.conf
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
3. Configure the miner to connect to your local node:

```
# Example T-Rex configuration
t-rex.exe -a kawpow -o stratum+tcp://127.0.0.1:3333 -u YOUR_SMELLYCOIN_ADDRESS -p x
```

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

```cmd
curl --user "YOUR_USERNAME:YOUR_PASSWORD" --data-binary "{\"jsonrpc\":\"1.0\",\"id\":\"curltest\",\"method\":\"getblockchaininfo\",\"params\":[]}" -H "content-type: text/plain" http://127.0.0.1:8332/
```

### Using PowerShell

```powershell
$headers = @{}
$auth = [System.Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("YOUR_USERNAME:YOUR_PASSWORD"))
$headers.Add("Authorization", "Basic $auth")

$body = '{"jsonrpc":"1.0","id":"powershelltest","method":"getblockchaininfo","params":[]}'

Invoke-RestMethod -Uri "http://127.0.0.1:8332/" -Method Post -Body $body -ContentType "application/json" -Headers $headers
```

## Troubleshooting

### Node Won't Start

- Check if another process is using the configured ports
- Verify your configuration file syntax
- Check Windows Firewall settings
- Examine the log file at `%USERPROFILE%\.smellycoin\debug.log`

### Synchronization Issues

- Ensure your internet connection is stable
- Check if your firewall is blocking connections
- Try adding known peers in your configuration file:

```
addnode=NODE_IP:8333
```

### Mining Problems

- Verify your mining address is correct
- Check system resources (CPU/GPU usage, temperature)
- For GPU mining, ensure you have the latest drivers installed

### RPC Connection Issues

- Verify RPC username and password
- Check if the RPC server is running and bound to the correct address
- Ensure no firewall is blocking the RPC port

## Additional Resources

- [SmellyCoin GitHub Repository](https://github.com/smellycoin/smellycoinr)
- [SmellyCoin Discord Community](https://discord.gg/smellycoin)
- [SmellyCoin Documentation](https://docs.smellycoin.org)