//! JSON-based Storage Implementation for SmellyCoin
//!
//! This module provides a JSON-based storage implementation for SmellyCoin blockchain data,
//! including blocks, transactions, and the UTXO set. It is designed for simplicity and
//! ease of debugging, though it may not be as performant as other storage options for
//! large-scale deployments.

use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use async_trait::async_trait;
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use tokio::sync::RwLock;

use smellycoin_core::{Block, Hash, Transaction, UTXOSet};

use crate::BlockStore;
use crate::StorageError;

/// JSON-based block store implementation
pub struct JsonBlockStore {
    /// Base directory for all data
    base_dir: PathBuf,
    
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

impl JsonBlockStore {
    /// Create a new JSON block store
    pub async fn new(base_dir: PathBuf) -> Result<Self, StorageError> {
        // Create directories if they don't exist
        let blocks_dir = base_dir.join("blocks");
        let utxo_dir = base_dir.join("utxo");
        let tx_dir = base_dir.join("transactions");
        
        fs::create_dir_all(&blocks_dir).map_err(StorageError::Io)?;
        fs::create_dir_all(&utxo_dir).map_err(StorageError::Io)?;
        fs::create_dir_all(&tx_dir).map_err(StorageError::Io)?;
        
        // Initialize data structures
        let blocks = tokio::sync::RwLock::new(HashMap::new());
        let block_hashes = tokio::sync::RwLock::new(HashMap::new());
        let best_block_hash = tokio::sync::RwLock::new([0; 32]);
        let utxo_set = tokio::sync::RwLock::new(UTXOSet::new(true)); // Enable pruning
        let transactions = tokio::sync::RwLock::new(HashMap::new());
        let tx_blocks = tokio::sync::RwLock::new(HashMap::new());
        
        // Load existing data if available
        let store = JsonBlockStore {
            base_dir,
            blocks,
            block_hashes,
            best_block_hash,
            utxo_set,
            transactions,
            tx_blocks,
        };
        
        store.load_data().await?;
        
        Ok(store)
    }
    
    /// Load existing data from disk
    async fn load_data(&self) -> Result<(), StorageError> {
        // Load blocks
        let blocks_dir = self.base_dir.join("blocks");
        if blocks_dir.exists() {
            for entry in fs::read_dir(&blocks_dir).map_err(StorageError::Io)? {
                let entry = entry.map_err(StorageError::Io)?;
                let path = entry.path();
                
                if path.is_file() && path.extension().map_or(false, |ext| ext == "json") {
                    let file = File::open(&path).map_err(StorageError::Io)?;
                    let reader = BufReader::new(file);
                    let block: Block = serde_json::from_reader(reader).map_err(StorageError::Serialization)?;
                    
                    let hash = block.hash();
                    let height = block.header.height;
                    
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
                }
            }
        }
        
        // Load best block hash
        let best_block_path = self.base_dir.join("best_block.json");
        if best_block_path.exists() {
            let file = File::open(&best_block_path).map_err(StorageError::Io)?;
            let reader = BufReader::new(file);
            let best_hash: Hash = serde_json::from_reader(reader).map_err(StorageError::Serialization)?;
            *self.best_block_hash.write().await = best_hash;
        }
        
        // Load UTXO set
        let utxo_path = self.base_dir.join("utxo").join("utxo_set.json");
        if utxo_path.exists() {
            let file = File::open(&utxo_path).map_err(StorageError::Io)?;
            let reader = BufReader::new(file);
            let utxo_set: UTXOSet = serde_json::from_reader(reader).map_err(StorageError::Serialization)?;
            *self.utxo_set.write().await = utxo_set;
        }
        
        Ok(())
    }
    
    /// Save a block to disk
    async fn save_block(&self, block: &Block) -> Result<(), StorageError> {
        let hash = block.hash();
        let hash_hex = hex::encode(hash);
        let block_path = self.base_dir.join("blocks").join(format!("{}.json", hash_hex));
        
        let file = File::create(&block_path).map_err(StorageError::Io)?;
        let writer = BufWriter::new(file);
        serde_json::to_writer_pretty(writer, block).map_err(StorageError::Serialization)?;
        
        Ok(())
    }
    
    /// Save the best block hash to disk
    async fn save_best_block_hash(&self, hash: &Hash) -> Result<(), StorageError> {
        let best_block_path = self.base_dir.join("best_block.json");
        
        let file = File::create(&best_block_path).map_err(StorageError::Io)?;
        let writer = BufWriter::new(file);
        serde_json::to_writer(writer, hash).map_err(StorageError::Serialization)?;
        
        Ok(())
    }
    
    /// Save the UTXO set to disk
    async fn save_utxo_set(&self, utxo_set: &UTXOSet) -> Result<(), StorageError> {
        let utxo_path = self.base_dir.join("utxo").join("utxo_set.json");
        
        let file = File::create(&utxo_path).map_err(StorageError::Io)?;
        let writer = BufWriter::new(file);
        serde_json::to_writer(writer, utxo_set).map_err(StorageError::Serialization)?;
        
        Ok(())
    }
}

#[async_trait]
impl BlockStore for JsonBlockStore {
    async fn store_block(&self, block: &Block) -> Result<(), StorageError> {
        let hash = block.hash();
        let height = block.header.height;
        
        // Store the block in memory
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
        
        // Save to disk
        self.save_block(block).await?;
        
        // If this is the first block, set it as the best block
        if height == 0 || self.blocks.read().await.len() == 1 {
            *self.best_block_hash.write().await = hash;
            self.save_best_block_hash(&hash).await?
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
        self.save_best_block_hash(hash).await
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
        self.save_utxo_set(utxo_set).await
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