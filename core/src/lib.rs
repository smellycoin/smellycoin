//! SmellyCoin Core Types and Data Structures
//!
//! This module defines the fundamental data structures used throughout the SmellyCoin
//! cryptocurrency, including blocks, transactions, and the UTXO model.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};
use thiserror::Error;

// Import modules from separate files
pub mod block;
pub mod transaction;
pub mod utxo;

/// Re-export core types
pub use block::{Block, BlockHeader, BlockValidationError};
pub use transaction::{Transaction, TransactionInput, TransactionOutput, TransactionValidationError};
pub use utxo::{UTXOSet, UTXOEntry, UTXOError};

/// Hash type used throughout the system
pub type Hash = [u8; 32];

/// Address type (public key hash)
pub type Address = [u8; 20];

/// Network types supported by SmellyCoin
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Network {
    /// Main production network
    Mainnet,
    /// Test network for development
    Testnet,
    /// Local regression testing
    Regtest,
}

impl Default for Network {
    fn default() -> Self {
        Network::Mainnet
    }
}

impl fmt::Display for Network {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Network::Mainnet => write!(f, "mainnet"),
            Network::Testnet => write!(f, "testnet"),
            Network::Regtest => write!(f, "regtest"),
        }
    }
}

/// Core error types
#[derive(Error, Debug)]
pub enum CoreError {
    #[error("Block validation error: {0}")]
    BlockValidation(#[from] BlockValidationError),
    
    #[error("Transaction validation error: {0}")]
    TransactionValidation(#[from] TransactionValidationError),
    
    #[error("UTXO error: {0}")]
    UTXO(#[from] UTXOError),
    
    #[error("Serialization error: {0}")]
    Serialization(String),
    
    #[error("Invalid hash: {0}")]
    InvalidHash(String),
    
    #[error("Invalid address: {0}")]
    InvalidAddress(String),
    
    #[error("Chain state error: {0}")]
    ChainState(String),
}

/// Get current timestamp in seconds
pub fn current_timestamp() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("Time went backwards")
        .as_secs()
}

// Block implementation details
mod block_impl {
    use super::*;
    use std::fmt;
    
    /// Block header structure
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct BlockHeader {
        /// Block version
        pub version: u32,
        /// Hash of the previous block
        pub prev_block_hash: Hash,
        /// Merkle root of transactions
        pub merkle_root: Hash,
        /// Block timestamp
        pub timestamp: u64,
        /// Difficulty target
        pub bits: u32,
        /// Nonce for proof-of-work
        pub nonce: u64,
        /// Height of the block in the chain
        pub height: u64,
    }
    
    impl BlockHeader {
        /// Calculate the hash of this block header
        pub fn hash(&self) -> Hash {
            // Placeholder for actual hash calculation
            // In a real implementation, this would serialize the header and hash it
            [0u8; 32]
        }
    }
    
    /// Complete block structure
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct Block {
        /// Block header
        pub header: BlockHeader,
        /// Transactions included in this block
        pub transactions: Vec<Transaction>,
    }
    
    impl Block {
        /// Create a new block with the given parameters
        pub fn new(
            version: u32,
            prev_block_hash: Hash,
            merkle_root: Hash,
            timestamp: u64,
            bits: u32,
            nonce: u64,
            height: u64,
            transactions: Vec<Transaction>,
        ) -> Self {
            let header = BlockHeader {
                version,
                prev_block_hash,
                merkle_root,
                timestamp,
                bits,
                nonce,
                height,
            };
            
            Block {
                header,
                transactions,
            }
        }
        
        /// Calculate the hash of this block
        pub fn hash(&self) -> Hash {
            self.header.hash()
        }
        
        /// Calculate the merkle root of the transactions
        pub fn calculate_merkle_root(&self) -> Hash {
            // Placeholder for actual merkle root calculation
            // In a real implementation, this would build a merkle tree from transaction hashes
            [0u8; 32]
        }
        
        /// Validate this block
        pub fn validate(&self, _network: Network) -> Result<(), BlockValidationError> {
            // Check that the merkle root matches the transactions
            let calculated_merkle_root = self.calculate_merkle_root();
            if calculated_merkle_root != self.header.merkle_root {
                return Err(BlockValidationError::InvalidMerkleRoot);
            }
            
            // Validate proof of work
            // TODO: Implement KAWPOW validation
            
            // Validate transactions
            for tx in &self.transactions {
                tx.validate(
                    |_hash, _index| Some(0), // Placeholder for get_output_value
                    |_input, _hash| true,    // Placeholder for verify_signature
                    0,                     // Placeholder for min_fee_rate
                    1_000_000              // Placeholder for max_size
                )?;
            }
            
            Ok(())
        }
    }
    
    /// Block validation errors
    #[derive(Error, Debug)]
    pub enum BlockValidationError {
        #[error("Invalid merkle root")]
        InvalidMerkleRoot,
        
        #[error("Invalid proof of work")]
        InvalidProofOfWork,
        
        #[error("Invalid timestamp")]
        InvalidTimestamp,
        
        #[error("Invalid difficulty")]
        InvalidDifficulty,
        
        #[error("Transaction validation error: {0}")]
        TransactionValidation(#[from] TransactionValidationError),
        
        #[error("Block too large")]
        BlockTooLarge,
        
        #[error("Invalid coinbase transaction")]
        InvalidCoinbase,
    }
}

// Transaction implementation details
mod transaction_impl {
    use super::*;
    
    /// Transaction input structure
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct TransactionInput {
        /// Reference to the previous transaction output
        pub prev_txid: Hash,
        /// Index of the output in the previous transaction
        pub prev_vout: u32,
        /// Script that satisfies the conditions of the output script
        pub script_sig: Vec<u8>,
        /// Sequence number
        pub sequence: u32,
    }
    
    /// Transaction output structure
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct TransactionOutput {
        /// Value in smallest units (satoshis)
        pub value: u64,
        /// Script that specifies the conditions to spend this output
        pub script_pubkey: Vec<u8>,
    }
    
    /// Transaction structure
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct Transaction {
        /// Transaction version
        pub version: u32,
        /// Transaction inputs
        pub inputs: Vec<TransactionInput>,
        /// Transaction outputs
        pub outputs: Vec<TransactionOutput>,
        /// Transaction lock time
        pub lock_time: u32,
    }
    
    impl Transaction {
        /// Create a new transaction
        pub fn new(
            version: u32,
            inputs: Vec<TransactionInput>,
            outputs: Vec<TransactionOutput>,
            lock_time: u32,
        ) -> Self {
            Transaction {
                version,
                inputs,
                outputs,
                lock_time,
            }
        }
        
        /// Calculate the transaction hash
        pub fn hash(&self) -> Hash {
            // Placeholder for actual hash calculation
            // In a real implementation, this would serialize the transaction and hash it
            [0u8; 32]
        }
        
        /// Check if this is a coinbase transaction
        pub fn is_coinbase(&self) -> bool {
            self.inputs.len() == 1 && self.inputs[0].prev_txid == [0u8; 32] && self.inputs[0].prev_vout == 0xFFFFFFFF
        }
        
        /// Validate this transaction
        pub fn validate<F, G>(
            &self,
            get_output_value: F,
            verify_signature: G,
            min_fee_rate: u64,
            max_size: usize,
        ) -> Result<(), TransactionValidationError>
        where
            F: Fn(&Hash, u32) -> Option<u64>,
            G: Fn(&TransactionInput, &Hash) -> bool,
        {
            // Basic validation
            if self.inputs.is_empty() {
                return Err(TransactionValidationError::NoInputs);
            }
            
            if self.outputs.is_empty() {
                return Err(TransactionValidationError::NoOutputs);
            }
            
            // Check for negative or overflow output values
            let mut total_output = 0u64;
            for output in &self.outputs {
                if output.value == 0 {
                    return Err(TransactionValidationError::ZeroOutput);
                }
                
                // Check for overflow
                match total_output.checked_add(output.value) {
                    Some(new_total) => total_output = new_total,
                    None => return Err(TransactionValidationError::OutputOverflow),
                }
            }
            
            // Coinbase-specific validation
            if self.is_coinbase() {
                // Additional coinbase validation rules would go here
                return Ok(());
            }
            
            // Non-coinbase validation
            // Note: Full validation would require access to the UTXO set
            // to verify input values and scripts
            
            Ok(())
        }
    }
    
    /// Transaction validation errors
    #[derive(Error, Debug)]
    pub enum TransactionValidationError {
        #[error("No inputs")]
        NoInputs,
        
        #[error("No outputs")]
        NoOutputs,
        
        #[error("Zero value output")]
        ZeroOutput,
        
        #[error("Output value overflow")]
        OutputOverflow,
        
        #[error("Input not found")]
        InputNotFound,
        
        #[error("Script validation failed")]
        ScriptValidationFailed,
        
        #[error("Invalid signature")]
        InvalidSignature,
        
        #[error("Insufficient funds")]
        InsufficientFunds,
        
        #[error("Double spend")]
        DoubleSpend,
    }
}

// UTXO set implementation details
mod utxo_impl {
    use super::*;
    
    /// UTXO entry representing an unspent transaction output
    #[derive(Clone, Debug, Serialize, Deserialize)]
    pub struct UTXOEntry {
        /// Transaction hash
        pub tx_hash: Hash,
        /// Output index
        pub output_index: u32,
        /// Output value
        pub value: u64,
        /// Script pubkey
        pub script_pubkey: Vec<u8>,
        /// Height of the block containing this UTXO
        pub height: u64,
        /// Whether this output is from a coinbase transaction
        pub is_coinbase: bool,
    }
    
    /// UTXO set for managing unspent transaction outputs
    #[derive(Debug, Default)]
    pub struct UTXOSet {
        /// Map of outpoint (tx_hash + output_index) to UTXO entry
        utxos: HashMap<(Hash, u32), UTXOEntry>,
    }
    
    impl UTXOSet {
        /// Create a new empty UTXO set
        pub fn new() -> Self {
            UTXOSet {
                utxos: HashMap::new(),
            }
        }
        
        /// Add a UTXO to the set
        pub fn add(&mut self, entry: UTXOEntry) {
            self.utxos.insert((entry.tx_hash, entry.output_index), entry);
        }
        
        /// Remove a UTXO from the set
        pub fn remove(&mut self, tx_hash: &Hash, output_index: u32) -> Option<UTXOEntry> {
            self.utxos.remove(&(*tx_hash, output_index))
        }
        
        /// Check if a UTXO exists
        pub fn contains(&self, tx_hash: &Hash, output_index: u32) -> bool {
            self.utxos.contains_key(&(*tx_hash, output_index))
        }
        
        /// Get a UTXO entry
        pub fn get(&self, tx_hash: &Hash, output_index: u32) -> Option<&UTXOEntry> {
            self.utxos.get(&(*tx_hash, output_index))
        }
        
        /// Get the total number of UTXOs
        pub fn len(&self) -> usize {
            self.utxos.len()
        }
        
        /// Check if the UTXO set is empty
        pub fn is_empty(&self) -> bool {
            self.utxos.is_empty()
        }
        
        /// Apply a block to the UTXO set (add outputs, remove spent inputs)
        pub fn apply_block(&mut self, block: &Block) -> Result<(), UTXOError> {
            for (tx_index, tx) in block.transactions.iter().enumerate() {
                let is_coinbase = tx_index == 0;
                
                // Remove spent inputs (except for coinbase)
                if !is_coinbase {
                    for input in &tx.inputs {
                        if !self.contains(&input.prev_txid, input.prev_vout) {
                            return Err(UTXOError::InputNotFound);
                        }
                        self.remove(&input.prev_txid, input.prev_vout);
                    }
                }
                
                // Add new outputs
                let tx_hash = tx.hash();
                for (output_index, output) in tx.outputs.iter().enumerate() {
                    let entry = UTXOEntry {
                        tx_hash,
                        output_index: output_index as u32,
                        value: output.value,
                        script_pubkey: output.script_pubkey.clone(),
                        height: block.height.unwrap_or(0),
                        is_coinbase,
                    };
                    self.add(entry);
                }
            }
            
            Ok(())
        }
        
        /// Revert a block from the UTXO set (remove outputs, add back inputs)
        pub fn revert_block(&mut self, block: &Block) -> Result<(), UTXOError> {
            // Remove outputs created in this block
            for tx in &block.transactions {
                let tx_hash = tx.hash();
                for output_index in 0..tx.outputs.len() {
                    self.remove(&tx_hash, output_index as u32);
                }
            }
            
            // Add back spent inputs (except for coinbase)
            // Note: This requires access to the previous state of these UTXOs
            // In a real implementation, this would require additional data structures
            
            Ok(())
        }
    }
    
    /// UTXO errors
    #[derive(Error, Debug)]
    pub enum UTXOError {
        #[error("Input not found")]
        InputNotFound,
        
        #[error("UTXO already exists")]
        UTXOAlreadyExists,
        
        #[error("Storage error: {0}")]
        Storage(String),
    }
}