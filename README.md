# SmellyCoin

A high-performance, GPU-mineable cryptocurrency built in Rust with KAWPOW algorithm.

## Overview

SmellyCoin is a modern cryptocurrency designed with performance, accessibility, and future-proofing in mind. It leverages Rust's safety and performance features to create a robust blockchain platform optimized for consumer-grade GPU mining.

### Key Features

- **Fast Block Times**: 15-second block time for rapid transaction confirmation
- **KAWPOW Algorithm**: ASIC-resistant, GPU-optimized mining algorithm
- **UTXO Model**: Efficient transaction handling and state management
- **Resource Efficiency**: Aggressive state pruning and lightweight client support
- **Cross-Platform**: Easy compilation on Linux, macOS, and Windows
- **Developer-Friendly**: Comprehensive documentation and clear APIs
- **Future-Ready**: Architecture designed for potential zk-SNARK integration

## Architecture

SmellyCoin is built with a modular architecture consisting of the following core components:

### Core Components

1. **Networking Layer**
   - Peer discovery and management
   - Block and transaction propagation
   - NAT traversal and connection management
   - Optimized for fast block propagation

2. **Consensus Engine**
   - KAWPOW Proof-of-Work implementation
   - Block validation and chain selection
   - Difficulty adjustment algorithm
   - Fork resolution

3. **Mempool Management**
   - Transaction validation and prioritization
   - Fee estimation
   - Memory-efficient transaction storage

4. **Storage Layer**
   - UTXO set management
   - Block and transaction storage
   - State pruning capabilities
   - Database abstraction for flexibility

5. **RPC Server**
   - JSON-RPC API for wallet integration
   - Mining interfaces (getblocktemplate)
   - Stratum protocol support for mining pools
   - Optional REST/WebSocket API

6. **KAWPOW Interface**
   - Optimized GPU mining support
   - Algorithm parameter management
   - Verification optimizations

## Getting Started

### Prerequisites

- Rust toolchain (1.75.0 or newer)
- Cargo package manager
- GPU with OpenCL or CUDA support (for mining)

### Building from Source

```bash
# Clone the repository
git clone https://github.com/smellycoin/smellycoinr.git
cd smellycoinr

# Build in release mode
cargo build --release

# Run the node
./target/release/smellycoin
```

### Configuration

SmellyCoin can be configured via command-line arguments or a configuration file:

```bash
# Run with custom configuration file
./target/release/smellycoin --config my_config.toml

# Run with specific options
./target/release/smellycoin --network mainnet --rpc-bind 127.0.0.1:8332 --mining-address smcXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

### Node Setup

SmellyCoin supports two types of node setup: creating a new blockchain (genesis node) or joining an existing network. Detailed instructions are available in the [node setup documentation](docs/node_setup.md).

#### Genesis Node

To create a new blockchain:

```bash
# Initialize the blockchain
./target/release/smellycoin init --force

# Start the node with mining enabled
./target/release/smellycoin --mine --mining-address=YOUR_SMELLYCOIN_ADDRESS
```

#### Joining Existing Network

To join an existing SmellyCoin network, simply configure your node with seed peers and start it:

```bash
./target/release/smellycoin
```

The node will automatically begin synchronizing with the network.

## Mining

SmellyCoin supports mining through standard KAWPOW-compatible mining software:

### Solo Mining

```bash
# Start the node with mining enabled
./target/release/smellycoin --mine --mining-address YOUR_ADDRESS --threads 1
```

### Pool Mining

Use any KAWPOW-compatible mining software (nbminer, T-Rex, teamredminer) with the following settings:

```
Algorithm: KAWPOW
Pool URL: stratum+tcp://pool_address:port
Wallet: YOUR_SMELLYCOIN_ADDRESS
```

## Documentation

Comprehensive documentation is available in the `/docs` directory and covers:

- [Architecture Overview](docs/architecture.md)
- [API Reference](docs/api/README.md)
- [Mining Guide](docs/mining.md)
- [Node Configuration](docs/configuration.md)
- [Developer Guide](docs/development.md)

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

SmellyCoin is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.