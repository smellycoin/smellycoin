# SmellyCoin Database Integration Guide

## Overview

SmellyCoin now supports a high-performance database backend for blockchain storage, optimized for speed and efficiency. This document explains how to use the database integration and its benefits for mining and blockchain operations.

## Features

- **SQLite-based storage**: Fast, reliable, and embedded database that doesn't require a separate server
- **Block header optimization**: Specialized storage for block headers to support lightweight mining
- **Reduced memory footprint**: Efficient storage that allows running nodes on devices with limited resources
- **Improved query performance**: Fast lookups for blocks, transactions, and UTXO data
- **Durability**: Crash-resistant storage with transaction support

## Configuration

To use the database backend, update your `smellycoin.conf` file with the following settings:

```
# Storage settings
storage=database  # Use database backend instead of JSON files
database_path=/path/to/database  # Path to store the database files
```

## Mining with Block Headers

The database implementation includes specialized support for mining with block headers only, which significantly reduces bandwidth and storage requirements:

1. **Lightweight mining**: Miners only need block headers (~80 bytes) instead of full blocks (potentially megabytes)
2. **Faster synchronization**: New miners can start mining quickly without downloading the entire blockchain
3. **Reduced network traffic**: Mining pools can distribute work more efficiently

### How It Works

1. The database stores block headers separately from full blocks
2. Mining operations use the headers to construct proof-of-work puzzles
3. When a block is found, only then is the full block data needed

## Performance Considerations

- **Indexing**: The database automatically creates indexes for fast lookups by block height, hash, and transaction ID
- **Caching**: Frequently accessed data is cached in memory for improved performance
- **Pruning**: Old transaction data can be pruned while maintaining the UTXO set for validation

## Migration from JSON Storage

To migrate existing blockchain data from the JSON storage to the database:

1. Stop your SmellyCoin node
2. Run the migration tool:
   ```bash
   ./target/release/smellycoin --migrate-storage
   ```
3. Update your configuration to use the database backend
4. Restart your node

## Database Maintenance

### Backup

It's recommended to regularly back up your database file:

```bash
sqlite3 /path/to/database/smellycoin.db .dump > backup.sql
```

### Optimization

Periodically optimize the database to maintain performance:

```bash
sqlite3 /path/to/database/smellycoin.db "VACUUM;"
```

## Technical Details

The database implementation uses the following schema:

- **blocks**: Stores full block data
- **block_headers**: Stores only block headers for efficient mining
- **transactions**: Stores transaction data with references to their containing blocks
- **utxo**: Stores the current UTXO set for fast validation
- **metadata**: Stores blockchain metadata like the best block hash

## Integration with Mining Pools

Mining pools benefit significantly from the database backend:

1. **Faster job generation**: Quick access to block headers for creating mining jobs
2. **Efficient share validation**: Fast verification of submitted shares
3. **Improved scalability**: Support for more concurrent miners with lower resource usage

## Web and Mobile Integration

The database backend works seamlessly with the WASM-based web mining and wallet functionality:

1. **Lightweight API**: Web clients can request minimal data for operations
2. **Header-based synchronization**: Mobile wallets can sync quickly using only headers
3. **Efficient queries**: Web wallets can retrieve transaction history efficiently

## Conclusion

The database integration provides a significant performance improvement for SmellyCoin, enabling faster synchronization, more efficient mining, and better support for resource-constrained devices. By using block headers for mining operations, the system can scale to support more users while maintaining strong security and decentralization.