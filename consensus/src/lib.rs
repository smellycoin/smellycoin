//! SmellyCoin Consensus Engine
//!
//! This module implements the consensus rules for SmellyCoin, including the
//! KAWPOW proof-of-work algorithm and difficulty adjustment mechanism.
//! It is optimized for GPU mining with a 15-second block time target.

use log::{debug, info, warn};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha3::{Digest, Keccak256};
use std::sync::Arc;
use thiserror::Error;

use smellycoin_core::{Block, BlockHeader, Hash, Network};

pub mod difficulty;
pub mod kawpow;

/// Re-export consensus types
pub use difficulty::{calculate_next_difficulty, get_difficulty_for_bits};
pub use kawpow::{verify_kawpow, KawpowContext, KawpowError, KawpowParams};

/// Consensus parameters for different networks
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConsensusParams {
    /// Network type
    pub network: Network,
    /// Block time target in seconds
    pub target_block_time: u64,
    /// Blocks per difficulty adjustment
    pub difficulty_adjustment_interval: u64,
    /// Maximum block size in bytes
    pub max_block_size: usize,
    /// Minimum difficulty bits
    pub min_difficulty_bits: u32,
    /// Initial difficulty bits
    pub initial_difficulty_bits: u32,
    /// KAWPOW algorithm parameters
    pub kawpow_params: KawpowParams,
    /// Block subsidy halving interval
    pub subsidy_halving_interval: u64,
    /// Initial block subsidy in smallest units
    pub initial_block_subsidy: u64,
}

impl ConsensusParams {
    /// Get consensus parameters for mainnet
    pub fn mainnet() -> Self {
        ConsensusParams {
            network: Network::Mainnet,
            target_block_time: 15,  // 15 seconds
            difficulty_adjustment_interval: 2016, // ~8.4 hours
            max_block_size: 2_000_000, // 2MB
            min_difficulty_bits: 0x1e00ffff,
            initial_difficulty_bits: 0x1e00ffff,
            kawpow_params: KawpowParams::mainnet(),
            subsidy_halving_interval: 2_100_000, // ~1 year
            initial_block_subsidy: 50_000_000_000, // 500 SMC
        }
    }

    /// Get consensus parameters for testnet
    pub fn testnet() -> Self {
        ConsensusParams {
            network: Network::Testnet,
            target_block_time: 15,  // 15 seconds
            difficulty_adjustment_interval: 2016, // ~8.4 hours
            max_block_size: 2_000_000, // 2MB
            min_difficulty_bits: 0x1e00ffff,
            initial_difficulty_bits: 0x1e00ffff,
            kawpow_params: KawpowParams::testnet(),
            subsidy_halving_interval: 2_100_000, // ~1 year
            initial_block_subsidy: 50_000_000_000, // 500 SMC
        }
    }

    /// Get consensus parameters for regtest
    pub fn regtest() -> Self {
        ConsensusParams {
            network: Network::Regtest,
            target_block_time: 15,  // 15 seconds
            difficulty_adjustment_interval: 144, // ~36 minutes
            max_block_size: 2_000_000, // 2MB
            min_difficulty_bits: 0x207fffff, // Very low difficulty for testing
            initial_difficulty_bits: 0x207fffff,
            kawpow_params: KawpowParams::regtest(),
            subsidy_halving_interval: 150, // For testing
            initial_block_subsidy: 50_000_000_000, // 500 SMC
        }
    }

    /// Get block subsidy for a given height
    pub fn get_block_subsidy(&self, height: u64) -> u64 {
        let halvings = height / self.subsidy_halving_interval;
        
        // No more subsidy after 64 halvings
        if halvings >= 64 {
            return 0;
        }
        
        // Shift right by halvings (dividing by 2^halvings)
        self.initial_block_subsidy >> halvings
    }
}

/// Consensus engine for validating blocks and managing the chain
#[derive(Debug)]
pub struct ConsensusEngine {
    /// Consensus parameters
    params: ConsensusParams,
    /// KAWPOW context for verification
    kawpow_context: Arc<KawpowContext>,
}

impl ConsensusEngine {
    /// Create a new consensus engine with the given parameters
    pub fn new(params: ConsensusParams) -> Self {
        let kawpow_context = Arc::new(KawpowContext::new(params.kawpow_params.clone()));
        
        ConsensusEngine {
            params,
            kawpow_context,
        }
    }
    
    /// Validate a block according to consensus rules
    pub fn validate_block(&self, block: &Block, prev_header: Option<&BlockHeader>) -> Result<(), ConsensusError> {
        // Check block size
        // In a real implementation, this would serialize the block and check its size
        
        // Validate proof of work
        self.validate_pow(&block.header)?;
        
        // Validate difficulty if we have the previous header
        if let Some(prev) = prev_header {
            self.validate_difficulty(&block.header, prev)?;
        }
        
        // Validate coinbase transaction
        if !block.transactions.is_empty() {
            let coinbase = &block.transactions[0];
            if !coinbase.is_coinbase() {
                return Err(ConsensusError::InvalidCoinbase("First transaction must be coinbase".into()));
            }
            
            // Check coinbase value
            let subsidy = self.params.get_block_subsidy(block.header.height);
            let mut total_fees = 0; // In a real implementation, calculate from transactions
            
            let mut coinbase_output_value = 0;
            for output in &coinbase.outputs {
                coinbase_output_value += output.value;
            }
            
            if coinbase_output_value > subsidy + total_fees {
                return Err(ConsensusError::InvalidCoinbase("Coinbase value too high".into()));
            }
        } else {
            return Err(ConsensusError::NoTransactions);
        }
        
        // Validate all other transactions
        // In a real implementation, this would check against the UTXO set
        
        Ok(())
    }
    
    /// Validate the proof of work for a block header
    pub fn validate_pow(&self, header: &BlockHeader) -> Result<(), ConsensusError> {
        // Get the mix hash from the header
        let mix_hash = header.hash();
        
        // Verify KAWPOW
        match kawpow::verify_kawpow(
            &self.kawpow_context,
            &header.prev_block_hash,
            header.height,
            header.nonce,
            &mix_hash,
        ) {
            Ok(hash) => {
                // Check if the hash meets the difficulty target
                let target = difficulty::bits_to_target(header.bits);
                
                // Convert hash to a comparable format
                let hash_bytes = hash.as_ref();
                let mut is_valid = true;
                
                // Compare hash with target (target should be greater than hash)
                for i in (0..32).rev() {
                    if hash_bytes[i] < target[i] {
                        break;
                    } else if hash_bytes[i] > target[i] {
                        is_valid = false;
                        break;
                    }
                }
                
                if is_valid {
                    Ok(())
                } else {
                    Err(ConsensusError::InvalidProofOfWork("Hash doesn't meet target".into()))
                }
            },
            Err(e) => Err(ConsensusError::KawpowError(e)),
        }
    }
    
    /// Validate the difficulty of a block
    pub fn validate_difficulty(&self, header: &BlockHeader, prev_header: &BlockHeader) -> Result<(), ConsensusError> {
        // Check if we're at a difficulty adjustment interval
        if header.height % self.params.difficulty_adjustment_interval == 0 {
            // In a real implementation, this would calculate the expected difficulty
            // based on the time taken for the previous difficulty adjustment interval
            
            // For now, just check that the difficulty is valid
            if header.bits < self.params.min_difficulty_bits {
                return Err(ConsensusError::InvalidDifficulty("Difficulty too high".into()));
            }
        } else {
            // Not at an adjustment interval, difficulty should be the same
            if header.bits != prev_header.bits {
                return Err(ConsensusError::InvalidDifficulty("Unexpected difficulty change".into()));
            }
        }
        
        Ok(())
    }
    
    /// Get the consensus parameters
    pub fn params(&self) -> &ConsensusParams {
        &self.params
    }
}

/// Consensus errors
#[derive(Error, Debug)]
pub enum ConsensusError {
    #[error("Invalid proof of work: {0}")]
    InvalidProofOfWork(String),
    
    #[error("Invalid difficulty: {0}")]
    InvalidDifficulty(String),
    
    #[error("Invalid coinbase: {0}")]
    InvalidCoinbase(String),
    
    #[error("No transactions in block")]
    NoTransactions,
    
    #[error("Block too large")]
    BlockTooLarge,
    
    #[error("KAWPOW error: {0}")]
    KawpowError(#[from] KawpowError),
}

// The difficulty module is already defined as a separate file
// and imported at the top of this file

// The kawpow module is already defined as a separate file
// and imported at the top of this file
}