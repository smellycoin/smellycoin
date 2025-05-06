# SmellyCoin Database Module

This module provides a high-performance database implementation for the SmellyCoin blockchain, optimized for speed and efficiency. It uses SQLite for storage and includes specialized structures for block headers to support lightweight mining operations.

## Features

- **SQLite-based storage**: Fast, reliable, and embedded database that doesn't require a separate server
- **Block header optimization**: Specialized storage for block headers to support lightweight mining
- **Reduced memory footprint**: Efficient storage that allows running nodes on devices with limited resources
- **Improved query performance**: Fast lookups for blocks, transactions, and UTXO data
- **Durability**: Crash-resistant storage with transaction support

## Integration

To use the database backend in your SmellyCoin node:

1. Add the database module as a dependency in your project's Cargo.toml:

```toml
[dependencies]
smellycoin-database = { path = "../database" }
```

2. Initialize the database store in your application:

```rust
use smellycoin_database::SqliteBlockStore;
use std::path::PathBuf;

async fn init_storage() -> Result<Arc<dyn BlockStore>, Error> {
    let db_path = PathBuf::from("path/to/database/smellycoin.db");
    let store = SqliteBlockStore::new(db_path).await?
    Ok(Arc::new(store))
}
```

## Block Headers for Mining

The database implementation includes specialized support for mining with block headers only, which significantly reduces bandwidth and storage requirements. This is particularly useful for:

- Mining pools distributing work to miners
- Web-based mining through WASM
- Mobile mining applications

The `get_block_header` and `get_block_headers_by_height_range` methods provide efficient access to just the header data needed for mining operations.

## Performance Considerations

- The database automatically creates indexes for fast lookups
- Consider running periodic optimization with `VACUUM` for long-running nodes
- For high-throughput applications, adjust the SQLite page cache size

## Documentation

For more detailed information, see the [Database Integration Guide](../docs/database_integration.md).