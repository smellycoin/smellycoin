//! Block and BlockHeader Implementations for SmellyCoin
//!
//! This module defines the block and block header structures for SmellyCoin,
//! including validation logic and serialization/deserialization.

use chrono::{DateTime, TimeZone, Utc};
use serde::{Deserialize, Serialize};
use std::fmt;
use thiserror::Error;

use crate::{Hash, Transaction, TransactionValidationError};

/// Block validation errors
#[derive(Debug, Error)]
pub enum BlockValidationError {
    /// Invalid proof of work
    #[error("Invalid proof of work")]
    InvalidProofOfWork,
    
    /// Invalid merkle root
    #[error("Invalid merkle root")]
    InvalidMerkleRoot,
    
    /// Invalid timestamp
    #[error("Invalid timestamp: {0}")]
    InvalidTimestamp(String),
    
    /// Invalid transaction
    #[error("Invalid transaction: {0}")]
    InvalidTransaction(#[from] TransactionValidationError),
    
    /// Invalid block size
    #[error("Block exceeds maximum size")]
    ExceedsMaximumSize,
    
    /// Invalid coinbase transaction
    #[error("Invalid coinbase transaction: {0}")]
    InvalidCoinbase(String),
    
    /// Invalid previous block
    #[error("Invalid previous block: {0}")]
    InvalidPreviousBlock(String),
}

/// Block header
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlockHeader {
    /// Block version
    pub version: u32,
    
    /// Hash of the previous block
    pub prev_block_hash: Hash,
    
    /// Merkle root of transactions
    pub merkle_root: Hash,
    
    /// Block timestamp
    pub timestamp: u32,
    
    /// Difficulty bits
    pub bits: u32,
    
    /// Nonce for proof of work
    pub nonce: u64,
}

impl BlockHeader {
    /// Create a new block header
    pub fn new(
        version: u32,
        prev_block_hash: Hash,
        merkle_root: Hash,
        timestamp: u32,
        bits: u32,
        nonce: u64,
    ) -> Self {
        BlockHeader {
            version,
            prev_block_hash,
            merkle_root,
            timestamp,
            bits,
            nonce,
        }
    }
    
    /// Get the block hash
    pub fn hash(&self) -> Hash {
        // In a real implementation, this would calculate the hash
        // of the serialized block header using SHA-256
        // For simplicity, this is a placeholder
        let mut hash = [0; 32];
        hash[0] = (self.version & 0xff) as u8;
        hash[1] = ((self.version >> 8) & 0xff) as u8;
        hash[2] = ((self.version >> 16) & 0xff) as u8;
        hash[3] = ((self.version >> 24) & 0xff) as u8;
        hash
    }
    
    /// Get the block timestamp as a DateTime
    pub fn datetime(&self) -> DateTime<Utc> {
        Utc.timestamp_opt(self.timestamp as i64, 0).unwrap()
    }
}

impl fmt::Display for BlockHeader {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "BlockHeader {{ version: {}, prev: {}, merkle: {}, time: {}, bits: {:08x}, nonce: {} }}",
            self.version,
            hex::encode(&self.prev_block_hash[0..4]),
            hex::encode(&self.merkle_root[0..4]),
            self.datetime().format("%Y-%m-%d %H:%M:%S"),
            self.bits,
            self.nonce
        )
    }
}

/// Block
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Block {
    /// Block header
    pub header: BlockHeader,
    
    /// Transactions
    pub transactions: Vec<Transaction>,
    
    /// Block height (not part of the serialized block)
    #[serde(skip)]
    pub height: Option<u64>,
}

impl Block {
    /// Create a new block
    pub fn new(
        header: BlockHeader,
        transactions: Vec<Transaction>,
    ) -> Self {
        Block {
            header,
            transactions,
            height: None,
        }
    }
    
    /// Get the block hash
    pub fn hash(&self) -> Hash {
        self.header.hash()
    }
    
    /// Calculate the merkle root of the transactions
    pub fn calculate_merkle_root(&self) -> Hash {
        if self.transactions.is_empty() {
            return [0; 32];
        }
        
        // In a real implementation, this would calculate the merkle root
        // by hashing transaction IDs in a tree structure
        // For simplicity, this is a placeholder
        let mut hashes: Vec<Hash> = self.transactions.iter()
            .map(|tx| tx.txid)
            .collect();
            
        while hashes.len() > 1 {
            if hashes.len() % 2 != 0 {
                hashes.push(hashes.last().unwrap().clone());
            }
            
            let mut new_hashes = Vec::with_capacity(hashes.len() / 2);
            
            for i in 0..(hashes.len() / 2) {
                let mut combined = [0u8; 64];
                combined[0..32].copy_from_slice(&hashes[i * 2]);
                combined[32..64].copy_from_slice(&hashes[i * 2 + 1]);
                
                // Hash the combined hashes
                // In a real implementation, this would use SHA-256
                let mut hash = [0; 32];
                hash[0] = combined[0];
                hash[1] = combined[32];
                new_hashes.push(hash);
            }
            
            hashes = new_hashes;
        }
        
        hashes[0]
    }
    
    /// Validate the block
    pub fn validate(&self, max_block_size: usize) -> Result<(), BlockValidationError> {
        // Check block size
        let estimated_size = 80 + (self.transactions.len() * 250); // Rough estimate
        if estimated_size > max_block_size {
            return Err(BlockValidationError::ExceedsMaximumSize);
        }
        
        // Check merkle root
        let merkle_root = self.calculate_merkle_root();
        if merkle_root != self.header.merkle_root {
            return Err(BlockValidationError::InvalidMerkleRoot);
        }
        
        // Check timestamp
        let now = Utc::now().timestamp() as u32;
        if self.header.timestamp > now + 7200 { // 2 hours in the future
            return Err(BlockValidationError::InvalidTimestamp(
                format!("Block timestamp too far in the future: {}", self.header.timestamp)
            ));
        }
        
        // Check coinbase transaction
        if self.transactions.is_empty() {
            return Err(BlockValidationError::InvalidCoinbase(
                "Missing coinbase transaction".to_string()
            ));
        }
        
        // Validate all transactions
        for (i, tx) in self.transactions.iter().enumerate() {
            // First transaction must be coinbase
            if i == 0 {
                if tx.inputs.len() != 1 || tx.inputs[0].prev_txid != [0; 32] {
                    return Err(BlockValidationError::InvalidCoinbase(
                        "First transaction must be coinbase".to_string()
                    ));
                }
            } else {
                // Non-coinbase transactions must have valid inputs
                if tx.inputs.is_empty() {
                    return Err(BlockValidationError::InvalidTransaction(
                        TransactionValidationError::NoInputs
                    ));
                }
                
                // Check for coinbase maturity and double spends would be done here
                // in a full implementation
            }
            
            // Check outputs
            if tx.outputs.is_empty() {
                return Err(BlockValidationError::InvalidTransaction(
                    TransactionValidationError::NoOutputs
                ));
            }
            
            // Additional transaction validation would be done here
            // in a full implementation
        }
        
        // Proof of work validation would be done here
        // in a full implementation
        
        Ok(())
    }
    
    /// Get the total fees in the block
    pub fn get_total_fees(&self) -> u64 {
        // Skip coinbase transaction
        if self.transactions.len() <= 1 {
            return 0;
        }
        
        // In a real implementation, this would calculate the difference
        // between input and output values for each transaction
        // For simplicity, this is a placeholder
        self.transactions.len() as u64 * 1000 // 1000 satoshis per transaction
    }
    
    /// Get the coinbase reward for this block
    pub fn get_coinbase_reward(&self, subsidy: u64) -> u64 {
        subsidy + self.get_total_fees()
    }
}

impl fmt::Display for Block {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Block {{ height: {}, hash: {}, txs: {} }}",
            self.height.map_or("unknown".to_string(), |h| h.to_string()),
            hex::encode(&self.hash()[0..8]),
            self.transactions.len()
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{TransactionInput, TransactionOutput};
    
    // Helper function to create a test transaction
    fn create_test_tx(is_coinbase: bool) -> Transaction {
        let mut tx = Transaction {
            version: 1,
            inputs: Vec::new(),
            outputs: Vec::new(),
            lock_time: 0,
            txid: [0; 32], // Will be calculated
        };
        
        if is_coinbase {
            // Coinbase input
            tx.inputs.push(TransactionInput {
                prev_txid: [0; 32],
                prev_vout: 0xffffffff,
                script_sig: vec![1, 2, 3, 4], // Arbitrary data
                sequence: 0xffffffff,
            });
        } else {
            // Regular input
            let mut prev_txid = [0; 32];
            prev_txid[0] = 1;
            
            tx.inputs.push(TransactionInput {
                prev_txid,
                prev_vout: 0,
                script_sig: vec![0x30, 0x45, 0x02, 0x20], // Fake signature
                sequence: 0xffffffff,
            });
        }
        
        // Add an output
        tx.outputs.push(TransactionOutput {
            value: 50_000_000, // 0.5 SMC
            script_pubkey: vec![0x76, 0xa9, 0x14], // Simple script
        });
        
        // Calculate txid (simplified for testing)
        let mut txid = [0; 32];
        txid[0] = if is_coinbase { 0 } else { 1 };
        tx.txid = txid;
        
        tx
    }
    
    #[test]
    fn test_block_creation_and_validation() {
        // Create a block header
        let header = BlockHeader {
            version: 1,
            prev_block_hash: [0; 32],
            merkle_root: [0; 32], // Will be calculated
            timestamp: Utc::now().timestamp() as u32,
            bits: 0x1d00ffff,
            nonce: 0,
        };
        
        // Create transactions
        let coinbase_tx = create_test_tx(true);
        let regular_tx = create_test_tx(false);
        
        // Create a block
        let mut block = Block::new(
            header,
            vec![coinbase_tx, regular_tx],
        );
        
        // Calculate merkle root
        let merkle_root = block.calculate_merkle_root();
        block.header.merkle_root = merkle_root;
        
        // Validate the block
        let result = block.validate(1_000_000); // 1MB max size
        assert!(result.is_ok());
        
        // Test invalid merkle root
        let mut invalid_block = block.clone();
        invalid_block.header.merkle_root = [1; 32]; // Wrong merkle root
        let result = invalid_block.validate(1_000_000);
        assert!(matches!(result, Err(BlockValidationError::InvalidMerkleRoot)));
        
        // Test future timestamp
        let mut invalid_block = block.clone();
        invalid_block.header.timestamp = Utc::now().timestamp() as u32 + 10000; // Far future
        let result = invalid_block.validate(1_000_000);
        assert!(matches!(result, Err(BlockValidationError::InvalidTimestamp(_))));
        
        // Test missing coinbase
        let mut invalid_block = block.clone();
        invalid_block.transactions = vec![regular_tx]; // No coinbase
        let result = invalid_block.validate(1_000_000);
        assert!(matches!(result, Err(BlockValidationError::InvalidCoinbase(_))));
    }
}