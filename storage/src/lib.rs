//! SmellyCoin Blockchain Storage Module
//!
//! This module provides storage implementations for the SmellyCoin blockchain,
//! including block storage, transaction indexing, and UTXO set management.
//! The default implementation uses JSON files for simplicity and debugging.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use thiserror::Error;

pub mod json_store;

use smellycoin_core::{Block, Hash, Transaction, UTXOSet};

// Re-export storage implementations
pub use json_store::JsonBlockStore;

/// Storage error types
#[derive(Debug, Error)]
pub enum StorageError {
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    
    /// Serialization error
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    
    /// Database error
    #[error("Database error: {0}")]
    Database(String),
    
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

/// Block storage interface
#[async_trait]
pub trait BlockStore: Send + Sync {
    /// Store a block
    async fn store_block(&self, block: &Block) -> Result<(), StorageError>;
    
    /// Get a block by hash
    async fn get_block(&self, hash: &Hash) -> Result<Block, StorageError>;
    
    /// Check if a block exists
    async fn has_block(&self, hash: &Hash) -> Result<bool, StorageError>;
    
    /// Get a block hash by height
    async fn get_block_hash(&self, height: u64) -> Result<Hash, StorageError>;
    
    /// Get the best block hash
    async fn get_best_block_hash(&self) -> Result<Hash, StorageError>;
    
    /// Get the best block height
    async fn get_best_block_height(&self) -> Result<u64, StorageError>;
    
    /// Set the best block
    async fn set_best_block(&self, hash: &Hash) -> Result<(), StorageError>;
    
    /// Get blocks in height range (inclusive)
    async fn get_blocks_by_height_range(&self, start: u64, end: u64) -> Result<Vec<Block>, StorageError>;
    
    /// Get the UTXO set
    async fn get_utxo_set(&self) -> Result<UTXOSet, StorageError>;
    
    /// Update the UTXO set
    async fn update_utxo_set(&self, utxo_set: &UTXOSet) -> Result<(), StorageError>;
    
    /// Get a transaction by hash
    async fn get_transaction(&self, hash: &Hash) -> Result<Transaction, StorageError>;
    
    /// Check if a transaction exists
    async fn has_transaction(&self, hash: &Hash) -> Result<bool, StorageError>;
    
    /// Get the block hash containing a transaction
    async fn get_transaction_block(&self, tx_hash: &Hash) -> Result<Hash, StorageError>;
}

/// Memory-based block store implementation
pub struct MemoryBlockStore {
    /// Blocks by hash
    blocks: tokio::sync::RwLock<HashMap<Hash, Block>>,
    
    /// Block hashes by height
    block_hashes: tokio::sync::RwLock<HashMap<u64, Hash>>,
    
    /// Best block hash
    best_block_hash: tokio::sync::RwLock<Hash>,
    
    /// UTXO set
    utxo_set: tokio::sync::RwLock<UTXOSet>,
    
    /// Transactions by hash
    transactions: tokio::sync::RwLock<HashMap<Hash, Transaction>>,
    
    /// Transaction block mapping
    tx_blocks: tokio::sync::RwLock<HashMap<Hash, Hash>>,
}

impl MemoryBlockStore {
    /// Create a new memory block store
    pub fn new() -> Self {
        MemoryBlockStore {
            blocks: tokio::sync::RwLock::new(HashMap::new()),
            block_hashes: tokio::sync::RwLock::new(HashMap::new()),
            best_block_hash: tokio::sync::RwLock::new([0; 32]),
            utxo_set: tokio::sync::RwLock::new(UTXOSet::new()),
            transactions: tokio::sync::RwLock::new(HashMap::new()),
            tx_blocks: tokio::sync::RwLock::new(HashMap::new()),
        }
    }
}

#[async_trait]
impl BlockStore for MemoryBlockStore {
    async fn store_block(&self, block: &Block) -> Result<(), StorageError> {
        let hash = block.hash();
        let height = block.header.height;
        
        // Store the block
        self.blocks.write().await.insert(hash, block.clone());
        self.block_hashes.write().await.insert(height, hash);
        
        // Store transactions
        let mut transactions = self.transactions.write().await;
        let mut tx_blocks = self.tx_blocks.write().await;
        
        for tx in &block.transactions {
            let tx_hash = tx.hash();
            transactions.insert(tx_hash, tx.clone());
            tx_blocks.insert(tx_hash, hash);
        }
        
        // If this is the first block, set it as the best block
        if height == 0 || self.blocks.read().await.len() == 1 {
            *self.best_block_hash.write().await = hash;
        }
        
        Ok(())
    }
    
    async fn get_block(&self, hash: &Hash) -> Result<Block, StorageError> {
        self.blocks.read().await.get(hash).cloned()
            .ok_or_else(|| StorageError::NotFound(format!("Block not found: {}", hex::encode(hash))))
    }
    
    async fn has_block(&self, hash: &Hash) -> Result<bool, StorageError> {
        Ok(self.blocks.read().await.contains_key(hash))
    }
    
    async fn get_block_hash(&self, height: u64) -> Result<Hash, StorageError> {
        self.block_hashes.read().await.get(&height).cloned()
            .ok_or_else(|| StorageError::NotFound(format!("Block at height {} not found", height)))
    }
    
    async fn get_best_block_hash(&self) -> Result<Hash, StorageError> {
        Ok(*self.best_block_hash.read().await)
    }
    
    async fn get_best_block_height(&self) -> Result<u64, StorageError> {
        let hash = *self.best_block_hash.read().await;
        let block = self.get_block(&hash).await?;
        Ok(block.header.height)
    }
    
    async fn set_best_block(&self, hash: &Hash) -> Result<(), StorageError> {
        // Verify the block exists
        if !self.has_block(hash).await? {
            return Err(StorageError::NotFound(format!("Block not found: {}", hex::encode(hash))));
        }
        
        *self.best_block_hash.write().await = *hash;
        Ok(())
    }
    
    async fn get_blocks_by_height_range(&self, start: u64, end: u64) -> Result<Vec<Block>, StorageError> {
        let mut blocks = Vec::new();
        let block_hashes = self.block_hashes.read().await;
        let blocks_map = self.blocks.read().await;
        
        for height in start..=end {
            if let Some(hash) = block_hashes.get(&height) {
                if let Some(block) = blocks_map.get(hash) {
                    blocks.push(block.clone());
                }
            }
        }
        
        Ok(blocks)
    }
    
    async fn get_utxo_set(&self) -> Result<UTXOSet, StorageError> {
        Ok(self.utxo_set.read().await.clone())
    }
    
    async fn update_utxo_set(&self, utxo_set: &UTXOSet) -> Result<(), StorageError> {
        *self.utxo_set.write().await = utxo_set.clone();
        Ok(())
    }
    
    async fn get_transaction(&self, hash: &Hash) -> Result<Transaction, StorageError> {
        self.transactions.read().await.get(hash).cloned()
            .ok_or_else(|| StorageError::NotFound(format!("Transaction not found: {}", hex::encode(hash))))
    }
    
    async fn has_transaction(&self, hash: &Hash) -> Result<bool, StorageError> {
        Ok(self.transactions.read().await.contains_key(hash))
    }
    
    async fn get_transaction_block(&self, tx_hash: &Hash) -> Result<Hash, StorageError> {
        self.tx_blocks.read().await.get(tx_hash).cloned()
            .ok_or_else(|| StorageError::NotFound(format!("Transaction block not found: {}", hex::encode(tx_hash))))
    }
}

/// Storage configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageConfig {
    /// Data directory
    pub data_dir: String,
    
    /// Maximum cache size in MB
    pub cache_size_mb: usize,
    
    /// Whether to maintain a transaction index
    pub txindex: bool,
    
    /// Whether to prune old blocks
    pub prune: bool,
    
    /// Prune target size in MB (0 = no pruning)
    pub prune_target_mb: usize,
}

impl Default for StorageConfig {
    fn default() -> Self {
        StorageConfig {
            data_dir: ".smellycoin".to_string(),
            cache_size_mb: 450,
            txindex: false,
            prune: false,
            prune_target_mb: 0,
        }
    }
}

/// Create a block store based on configuration
pub fn create_block_store(config: &StorageConfig) -> Result<Arc<dyn BlockStore>, StorageError> {
    // For now, just return a memory-based store
    // In a real implementation, this would create a persistent store
    Ok(Arc::new(MemoryBlockStore::new()))
}