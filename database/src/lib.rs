//! SmellyCoin Database Module
//!
//! This module provides a high-performance database implementation for the SmellyCoin blockchain,
//! optimized for speed and efficiency. It uses SQLite for storage and includes specialized
//! structures for block headers to support lightweight mining operations.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use log::{debug, error, info, warn};
use rusqlite::{params, Connection, Result as SqliteResult};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::sync::Mutex;

use smellycoin_core::{Block, BlockHeader, Hash, Transaction, UTXOSet};
use smellycoin_storage::{BlockStore, StorageError};

/// Database error types
#[derive(Debug, Error)]
pub enum DatabaseError {
    /// SQLite error
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    
    /// Serialization error
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    
    /// Not found
    #[error("Not found: {0}")]
    NotFound(String),
    
    /// Already exists
    #[error("Already exists: {0}")]
    AlreadyExists(String),
    
    /// Invalid data
    #[error("Invalid data: {0}")]
    InvalidData(String),
}

impl From<DatabaseError> for StorageError {
    fn from(err: DatabaseError) -> Self {
        match err {
            DatabaseError::NotFound(msg) => StorageError::NotFound(msg),
            DatabaseError::AlreadyExists(msg) => StorageError::AlreadyExists(msg),
            DatabaseError::InvalidData(msg) => StorageError::InvalidData(msg),
            _ => StorageError::Database(err.to_string()),
        }
    }
}

/// SQLite-based block store implementation
pub struct SqliteBlockStore {
    /// Database connection
    conn: Arc<Mutex<Connection>>,
    
    /// Database file path
    db_path: PathBuf,
}

impl SqliteBlockStore {
    /// Create a new SQLite block store
    pub async fn new(db_path: PathBuf) -> Result<Self, DatabaseError> {
        // Ensure the parent directory exists
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        
        // Open the database connection
        let conn = Connection::open(&db_path)?;
        
        // Create tables if they don't exist
        Self::create_tables(&conn)?;
        
        Ok(SqliteBlockStore {
            conn: Arc::new(Mutex::new(conn)),
            db_path,
        })
    }
    
    /// Create database tables
    fn create_tables(conn: &Connection) -> Result<(), DatabaseError> {
        // Create blocks table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS blocks (
                hash BLOB PRIMARY KEY,
                height INTEGER NOT NULL,
                version INTEGER NOT NULL,
                prev_hash BLOB NOT NULL,
                merkle_root BLOB NOT NULL,
                timestamp INTEGER NOT NULL,
                bits INTEGER NOT NULL,
                nonce INTEGER NOT NULL,
                data BLOB NOT NULL
            )",
            [],
        )?;
        
        // Create block_headers table for lightweight mining
        conn.execute(
            "CREATE TABLE IF NOT EXISTS block_headers (
                hash BLOB PRIMARY KEY,
                height INTEGER NOT NULL,
                version INTEGER NOT NULL,
                prev_hash BLOB NOT NULL,
                merkle_root BLOB NOT NULL,
                timestamp INTEGER NOT NULL,
                bits INTEGER NOT NULL,
                nonce INTEGER NOT NULL
            )",
            [],
        )?;
        
        // Create transactions table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS transactions (
                hash BLOB PRIMARY KEY,
                block_hash BLOB NOT NULL,
                data BLOB NOT NULL,
                FOREIGN KEY(block_hash) REFERENCES blocks(hash)
            )",
            [],
        )?;
        
        // Create UTXO table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS utxo (
                tx_hash BLOB NOT NULL,
                output_index INTEGER NOT NULL,
                data BLOB NOT NULL,
                PRIMARY KEY (tx_hash, output_index)
            )",
            [],
        )?;
        
        // Create metadata table
        conn.execute(
            "CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL
            )",
            [],
        )?;
        
        // Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_blocks_height ON blocks(height)", [])?;
        conn.execute("CREATE INDEX IF NOT EXISTS idx_block_headers_height ON block_headers(height)", [])?;
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_block ON transactions(block_hash)", [])?;
        
        Ok(())
    }
    
    /// Store a block header
    async fn store_block_header(&self, header: &BlockHeader) -> Result<(), DatabaseError> {
        let conn = self.conn.lock().await;
        let hash = header.hash();
        
        conn.execute(
            "INSERT OR REPLACE INTO block_headers 
             (hash, height, version, prev_hash, merkle_root, timestamp, bits, nonce) 
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                hash.as_ref(),
                header.height,
                header.version,
                header.prev_hash.as_ref(),
                header.merkle_root.as_ref(),
                header.timestamp,
                header.bits,
                header.nonce,
            ],
        )?;
        
        Ok(())
    }
    
    /// Get a block header by hash
    async fn get_block_header(&self, hash: &Hash) -> Result<BlockHeader, DatabaseError> {
        let conn = self.conn.lock().await;
        
        let header = conn.query_row(
            "SELECT height, version, prev_hash, merkle_root, timestamp, bits, nonce 
             FROM block_headers WHERE hash = ?",
            params![hash.as_ref()],
            |row| {
                let height: u64 = row.get(0)?;
                let version: u32 = row.get(1)?;
                let prev_hash_blob: Vec<u8> = row.get(2)?;
                let merkle_root_blob: Vec<u8> = row.get(3)?;
                let timestamp: u32 = row.get(4)?;
                let bits: u32 = row.get(5)?;
                let nonce: u64 = row.get(6)?;
                
                let mut prev_hash = [0u8; 32];
                let mut merkle_root = [0u8; 32];
                
                prev_hash.copy_from_slice(&prev_hash_blob);
                merkle_root.copy_from_slice(&merkle_root_blob);
                
                Ok(BlockHeader {
                    height,
                    version,
                    prev_hash,
                    merkle_root,
                    timestamp,
                    bits,
                    nonce,
                })
            },
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                DatabaseError::NotFound(format!("Block header not found: {}", hex::encode(hash)))
            } else {
                e.into()
            }
        })?;
        
        Ok(header)
    }
    
    /// Get block headers in height range (inclusive)
    async fn get_block_headers_by_height_range(&self, start: u64, end: u64) -> Result<Vec<BlockHeader>, DatabaseError> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare(
            "SELECT hash, height, version, prev_hash, merkle_root, timestamp, bits, nonce 
             FROM block_headers WHERE height >= ? AND height <= ? ORDER BY height"
        )?;
        
        let headers = stmt.query_map(params![start, end], |row| {
            let hash_blob: Vec<u8> = row.get(0)?;
            let height: u64 = row.get(1)?;
            let version: u32 = row.get(2)?;
            let prev_hash_blob: Vec<u8> = row.get(3)?;
            let merkle_root_blob: Vec<u8> = row.get(4)?;
            let timestamp: u32 = row.get(5)?;
            let bits: u32 = row.get(6)?;
            let nonce: u64 = row.get(7)?;
            
            let mut hash = [0u8; 32];
            let mut prev_hash = [0u8; 32];
            let mut merkle_root = [0u8; 32];
            
            hash.copy_from_slice(&hash_blob);
            prev_hash.copy_from_slice(&prev_hash_blob);
            merkle_root.copy_from_slice(&merkle_root_blob);
            
            Ok(BlockHeader {
                height,
                version,
                prev_hash,
                merkle_root,
                timestamp,
                bits,
                nonce,
            })
        })?.collect::<Result<Vec<_>, _>>()?;
        
        Ok(headers)
    }
    
    /// Set the best block hash
    async fn set_best_block_hash(&self, hash: &Hash) -> Result<(), DatabaseError> {
        let conn = self.conn.lock().await;
        
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            params!["best_block_hash", hash.as_ref()],
        )?;
        
        Ok(())
    }
    
    /// Get the best block hash
    async fn get_best_block_hash(&self) -> Result<Hash, DatabaseError> {
        let conn = self.conn.lock().await;
        
        let hash_blob: Vec<u8> = conn.query_row(
            "SELECT value FROM metadata WHERE key = ?",
            params!["best_block_hash"],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                DatabaseError::NotFound("Best block hash not found".to_string())
            } else {
                e.into()
            }
        })?;
        
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&hash_blob);
        
        Ok(hash)
    }
}

#[async_trait]
impl BlockStore for SqliteBlockStore {
    async fn store_block(&self, block: &Block) -> Result<(), StorageError> {
        let conn = self.conn.lock().await;
        let hash = block.hash();
        let header = &block.header;
        
        // Serialize the block
        let block_data = serde_json::to_vec(block)?;
        
        // Begin transaction
        let tx = conn.transaction()?;
        
        // Store the block
        tx.execute(
            "INSERT OR REPLACE INTO blocks 
             (hash, height, version, prev_hash, merkle_root, timestamp, bits, nonce, data) 
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                hash.as_ref(),
                header.height,
                header.version,
                header.prev_hash.as_ref(),
                header.merkle_root.as_ref(),
                header.timestamp,
                header.bits,
                header.nonce,
                block_data,
            ],
        )?;
        
        // Store the block header
        tx.execute(
            "INSERT OR REPLACE INTO block_headers 
             (hash, height, version, prev_hash, merkle_root, timestamp, bits, nonce) 
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            params![
                hash.as_ref(),
                header.height,
                header.version,
                header.prev_hash.as_ref(),
                header.merkle_root.as_ref(),
                header.timestamp,
                header.bits,
                header.nonce,
            ],
        )?;
        
        // Store transactions
        for tx_obj in &block.transactions {
            let tx_hash = tx_obj.hash();
            let tx_data = serde_json::to_vec(tx_obj)?;
            
            tx.execute(
                "INSERT OR REPLACE INTO transactions (hash, block_hash, data) VALUES (?, ?, ?)",
                params![tx_hash.as_ref(), hash.as_ref(), tx_data],
            )?;
        }
        
        // If this is the first block or has a higher height than current best, update best block
        if header.height == 0 {
            tx.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                params!["best_block_hash", hash.as_ref()],
            )?;
        } else {
            let current_best = tx.query_row(
                "SELECT value FROM metadata WHERE key = ?",
                params!["best_block_hash"],
                |row| {
                    let blob: Vec<u8> = row.get(0)?;
                    Ok(blob)
                },
            );
            
            match current_best {
                Ok(best_hash_blob) => {
                    let mut best_hash = [0u8; 32];
                    best_hash.copy_from_slice(&best_hash_blob);
                    
                    let best_height: u64 = tx.query_row(
                        "SELECT height FROM blocks WHERE hash = ?",
                        params![best_hash_blob],
                        |row| row.get(0),
                    )?;
                    
                    if header.height > best_height {
                        tx.execute(
                            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                            params!["best_block_hash", hash.as_ref()],
                        )?;
                    }
                },
                Err(_) => {
                    // No best block yet, set this one
                    tx.execute(
                        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                        params!["best_block_hash", hash.as_ref()],
                    )?;
                }
            }
        }
        
        // Commit transaction
        tx.commit()?;
        
        Ok(())
    }
    
    async fn get_block(&self, hash: &Hash) -> Result<Block, StorageError> {
        let conn = self.conn.lock().await;
        
        let block_data: Vec<u8> = conn.query_row(
            "SELECT data FROM blocks WHERE hash = ?",
            params![hash.as_ref()],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                StorageError::NotFound(format!("Block not found: {}", hex::encode(hash)))
            } else {
                StorageError::Database(e.to_string())
            }
        })?;
        
        let block: Block = serde_json::from_slice(&block_data)?;
        Ok(block)
    }
    
    async fn has_block(&self, hash: &Hash) -> Result<bool, StorageError> {
        let conn = self.conn.lock().await;
        
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM blocks WHERE hash = ?",
            params![hash.as_ref()],
            |row| row.get(0),
        )?;
        
        Ok(count > 0)
    }
    
    async fn get_block_hash(&self, height: u64) -> Result<Hash, StorageError> {
        let conn = self.conn.lock().await;
        
        let hash_blob: Vec<u8> = conn.query_row(
            "SELECT hash FROM blocks WHERE height = ?",
            params![height],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                StorageError::NotFound(format!("Block at height {} not found", height))
            } else {
                StorageError::Database(e.to_string())
            }
        })?;
        
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&hash_blob);
        
        Ok(hash)
    }
    
    async fn get_best_block_hash(&self) -> Result<Hash, StorageError> {
        let conn = self.conn.lock().await;
        
        let hash_blob: Vec<u8> = conn.query_row(
            "SELECT value FROM metadata WHERE key = ?",
            params!["best_block_hash"],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                StorageError::NotFound("Best block hash not found".to_string())
            } else {
                StorageError::Database(e.to_string())
            }
        })?;
        
        let mut hash = [0u8; 32];
        hash.copy_from_slice(&hash_blob);
        
        Ok(hash)
    }
    
    async fn get_best_block_height(&self) -> Result<u64, StorageError> {
        let hash = self.get_best_block_hash().await?;
        let conn = self.conn.lock().await;
        
        let height: u64 = conn.query_row(
            "SELECT height FROM blocks WHERE hash = ?",
            params![hash.as_ref()],
            |row| row.get(0),
        )?;
        
        Ok(height)
    }
    
    async fn set_best_block(&self, hash: &Hash) -> Result<(), StorageError> {
        // Verify the block exists
        if !self.has_block(hash).await? {
            return Err(StorageError::NotFound(format!("Block not found: {}", hex::encode(hash))));
        }
        
        let conn = self.conn.lock().await;
        
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            params!["best_block_hash", hash.as_ref()],
        )?;
        
        Ok(())
    }
    
    async fn get_blocks_by_height_range(&self, start: u64, end: u64) -> Result<Vec<Block>, StorageError> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare(
            "SELECT data FROM blocks WHERE height >= ? AND height <= ? ORDER BY height"
        )?;
        
        let blocks = stmt.query_map(params![start, end], |row| {
            let data: Vec<u8> = row.get(0)?;
            let block: Block = serde_json::from_slice(&data)
                .map_err(|e| rusqlite::Error::FromSqlConversionFailure(0, rusqlite::types::Type::Blob, Box::new(e)))?;
            Ok(block)
        })?.collect::<Result<Vec<_>, _>>()?;
        
        Ok(blocks)
    }
    
    async fn get_utxo_set(&self) -> Result<UTXOSet, StorageError> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare("SELECT tx_hash, output_index, data FROM utxo")?;
        
        let rows = stmt.query_map([], |row| {
            let tx_hash_blob: Vec<u8> = row.get(0)?;
            let output_index: u32 = row.get(1)?;
            let data: Vec<u8> = row.get(2)?;
            
            Ok((tx_hash_blob, output_index, data))
        })?;
        
        let mut utxo_set = UTXOSet::new(true); // Enable pruning
        
        for row in rows {
            let (tx_hash_blob, output_index, data) = row?;
            let mut tx_hash = [0u8; 32];
            tx_hash.copy_from_slice(&tx_hash_blob);
            
            let utxo: smellycoin_core::UTXO = serde_json::from_slice(&data)?;
            utxo_set.add_utxo(tx_hash, output_index, utxo);
        }
        
        Ok(utxo_set)
    }
    
    async fn update_utxo_set(&self, utxo_set: &UTXOSet) -> Result<(), StorageError> {
        let conn = self.conn.lock().await;
        let tx = conn.transaction()?;
        
        // Clear existing UTXO table
        tx.execute("DELETE FROM utxo", [])?;
        
        // Insert new UTXOs
        for ((tx_hash, output_index), utxo) in utxo_set.iter() {
            let utxo_data = serde_json::to_vec(utxo)?;
            
            tx.execute(
                "INSERT INTO utxo (tx_hash, output_index, data) VALUES (?, ?, ?)",
                params![tx_hash.as_ref(), output_index, utxo_data],
            )?;
        }
        
        tx.commit()?;
        Ok(())
    }
    
    async fn get_transaction(&self, hash: &Hash) -> Result<Transaction, StorageError> {
        let conn = self.conn.lock().await;
        
        let tx_data: Vec<u8> = conn.query_row(
            "SELECT data FROM transactions WHERE hash = ?",
            params![hash.as_ref()],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                StorageError::NotFound(format!("Transaction not found: {}", hex::encode(hash)))
            } else {
                StorageError::Database(e.to_string())
            }
        })?;
        
        let tx: Transaction = serde_json::from_slice(&tx_data)?;
        Ok(tx)
    }
    
    async fn has_transaction(&self, hash: &Hash) -> Result<bool, StorageError> {
        let conn = self.conn.lock().await;
        
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM transactions WHERE hash = ?",
            params![hash.as_ref()],
            |row| row.get(0),
        )?;
        
        Ok(count > 0)
    }
    
    async fn get_transaction_block(&self, tx_hash: &Hash) -> Result<Hash, StorageError> {
        let conn = self.conn.lock().await;
        
        let block_hash_blob: Vec<u8> = conn.query_row(
            "SELECT block_hash FROM transactions WHERE hash = ?",
            params![tx_hash.as_ref()],
            |row| row.get(0),
        ).map_err(|e| {
            if let rusqlite::Error::QueryReturnedNoRows = e {
                StorageError::NotFound(format!("Transaction not found: {}", hex::encode(tx_hash)))
            } else {
                StorageError::Database(e.to_string())
            }
        })?;
        
        let mut block_hash = [0u8; 32];
        block_hash.copy_from_slice(&block_hash_blob);
        
        Ok(block_hash)
    }
}