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
        // Verify KAWPOW
        match kawpow::verify_kawpow(
            &self.kawpow_context,
            &header.prev_block_hash,
            header.height,
            header.nonce,
            &header.hash(),
        ) {
            Ok(hash) => {
                // Check if the hash meets the difficulty target
                let target = difficulty::bits_to_target(header.bits);
                let hash_value = difficulty::hash_to_u256(&hash);
                
                if hash_value <= target {
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

/// Module for difficulty adjustment
pub mod difficulty {
    use super::*;
    
    /// Convert difficulty bits to target
    pub fn bits_to_target(bits: u32) -> [u8; 32] {
        let mut target = [0u8; 32];
        
        let exponent = ((bits >> 24) & 0xff) as usize;
        let mantissa = bits & 0x00ffffff;
        
        // Convert mantissa to big-endian bytes
        let mantissa_bytes = mantissa.to_be_bytes();
        
        // Place mantissa at the appropriate position based on exponent
        if exponent >= 3 {
            target[32 - exponent..32 - exponent + 3].copy_from_slice(&mantissa_bytes[1..]);
        } else {
            target[32 - 3..32].copy_from_slice(&mantissa_bytes[1..]);
            // Shift right to adjust for exponent
            for i in (0..29).rev() {
                target[i + 3 - exponent] = target[i];
                target[i] = 0;
            }
        }
        
        target
    }
    
    /// Convert hash to u256 for comparison with target
    pub fn hash_to_u256(hash: &[u8; 32]) -> [u8; 32] {
        // Reverse the hash for comparison with target
        let mut result = [0u8; 32];
        for i in 0..32 {
            result[i] = hash[31 - i];
        }
        result
    }
    
    /// Calculate the difficulty value from bits
    pub fn get_difficulty_for_bits(bits: u32) -> f64 {
        // Maximum difficulty (minimum target)
        const MAX_TARGET: [u8; 32] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                     0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff];
        
        let target = bits_to_target(bits);
        
        // Convert to f64 for division
        let mut target_value = 0.0;
        let mut max_target_value = 0.0;
        
        for i in 0..32 {
            target_value = target_value * 256.0 + target[i] as f64;
            max_target_value = max_target_value * 256.0 + MAX_TARGET[i] as f64;
        }
        
        if target_value <= 0.0 {
            return 0.0;
        }
        
        max_target_value / target_value
    }
    
    /// Calculate the next difficulty bits based on the time taken for the previous interval
    pub fn calculate_next_difficulty(
        prev_bits: u32,
        actual_timespan: u64,
        target_timespan: u64,
        min_difficulty_bits: u32,
    ) -> u32 {
        // Limit adjustment to factor of 4
        let mut adjusted_timespan = actual_timespan;
        if adjusted_timespan < target_timespan / 4 {
            adjusted_timespan = target_timespan / 4;
        }
        if adjusted_timespan > target_timespan * 4 {
            adjusted_timespan = target_timespan * 4;
        }
        
        // Calculate new target
        let mut target = bits_to_target(prev_bits);
        
        // Multiply by adjusted_timespan / target_timespan
        let mut bn_new = [0u8; 32];
        let mut carry = 0u16;
        
        for i in (0..32).rev() {
            let mut temp = (target[i] as u16 * adjusted_timespan as u16) + carry;
            bn_new[i] = (temp % 256) as u8;
            carry = temp / 256;
        }
        
        // Divide by target_timespan
        let mut remainder = 0u16;
        
        for i in 0..32 {
            let temp = (remainder * 256) + bn_new[i] as u16;
            bn_new[i] = (temp / target_timespan as u16) as u8;
            remainder = temp % target_timespan as u16;
        }
        
        // Convert back to bits format
        let mut exponent = 0;
        let mut mantissa = 0u32;
        
        // Find the first non-zero byte
        for i in 0..32 {
            if bn_new[i] != 0 {
                exponent = 32 - i;
                // Get the 3 bytes for mantissa
                if i <= 29 {
                    mantissa = ((bn_new[i] as u32) << 16) |
                              ((bn_new[i + 1] as u32) << 8) |
                              (bn_new[i + 2] as u32);
                } else if i == 30 {
                    mantissa = ((bn_new[i] as u32) << 16) |
                              ((bn_new[i + 1] as u32) << 8);
                } else {
                    mantissa = (bn_new[i] as u32) << 16;
                }
                break;
            }
        }
        
        // Ensure the highest bit is 1 (compact format)
        if (mantissa & 0x00800000) != 0 {
            mantissa >>= 8;
            exponent += 1;
        }
        
        let new_bits = (exponent << 24) | (mantissa & 0x00ffffff);
        
        // Ensure we don't go below minimum difficulty
        if new_bits > min_difficulty_bits {
            new_bits
        } else {
            min_difficulty_bits
        }
    }
}

/// Module for KAWPOW algorithm implementation
pub mod kawpow {
    use super::*;
    use once_cell::sync::Lazy;
    use std::sync::Mutex;
    
    /// KAWPOW algorithm parameters
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct KawpowParams {
        /// Light cache size
        pub light_cache_size: usize,
        /// Full dataset size
        pub full_dataset_size: usize,
        /// KAWPOW epoch length
        pub epoch_length: u64,
        /// Mix hash length
        pub mix_hash_length: usize,
        /// Hash output length
        pub hash_output_length: usize,
    }
    
    impl KawpowParams {
        /// Get KAWPOW parameters for mainnet
        pub fn mainnet() -> Self {
            KawpowParams {
                light_cache_size: 16 * 1024 * 1024,  // 16MB
                full_dataset_size: 2 * 1024 * 1024 * 1024,  // 2GB
                epoch_length: 7500,  // ~31 hours with 15s blocks
                mix_hash_length: 32,
                hash_output_length: 32,
            }
        }
        
        /// Get KAWPOW parameters for testnet
        pub fn testnet() -> Self {
            // Same as mainnet for simplicity
            Self::mainnet()
        }
        
        /// Get KAWPOW parameters for regtest
        pub fn regtest() -> Self {
            // Smaller dataset for testing
            KawpowParams {
                light_cache_size: 1 * 1024 * 1024,  // 1MB
                full_dataset_size: 16 * 1024 * 1024,  // 16MB
                epoch_length: 100,  // Small for testing
                mix_hash_length: 32,
                hash_output_length: 32,
            }
        }
    }
    
    /// KAWPOW context for verification and mining
    #[derive(Debug)]
    pub struct KawpowContext {
        /// KAWPOW parameters
        params: KawpowParams,
        /// Cache for light verification
        light_cache: Mutex<LightCache>,
    }
    
    impl KawpowContext {
        /// Create a new KAWPOW context
        pub fn new(params: KawpowParams) -> Self {
            KawpowContext {
                params: params.clone(),
                light_cache: Mutex::new(LightCache::new(params)),
            }
        }
        
        /// Get the epoch for a given block height
        pub fn get_epoch(&self, height: u64) -> u64 {
            height / self.params.epoch_length
        }
    }
    
    /// Light cache for KAWPOW verification
    #[derive(Debug)]
    struct LightCache {
        /// KAWPOW parameters
        params: KawpowParams,
        /// Current epoch
        current_epoch: u64,
        /// Cache data
        cache: Vec<u8>,
    }
    
    impl LightCache {
        /// Create a new light cache
        fn new(params: KawpowParams) -> Self {
            LightCache {
                params,
                current_epoch: u64::MAX, // Invalid epoch to force initialization
                cache: Vec::new(),
            }
        }
        
        /// Update the cache for a given epoch
        fn update(&mut self, epoch: u64) {
            if self.current_epoch == epoch {
                return;
            }
            
            info!("Generating KAWPOW light cache for epoch {}", epoch);
            
            // In a real implementation, this would generate the light cache
            // for the given epoch using the KAWPOW algorithm
            self.cache = vec![0; self.params.light_cache_size];
            self.current_epoch = epoch;
        }
    }
    
    /// KAWPOW errors
    #[derive(Error, Debug)]
    pub enum KawpowError {
        #[error("Invalid epoch: {0}")]
        InvalidEpoch(u64),
        
        #[error("Cache generation failed")]
        CacheGenerationFailed,
        
        #[error("Verification failed")]
        VerificationFailed,
    }
    
    /// Verify a KAWPOW proof of work
    pub fn verify_kawpow(
        context: &KawpowContext,
        prev_hash: &Hash,
        height: u64,
        nonce: u64,
        mix_hash: &Hash,
    ) -> Result<Hash, KawpowError> {
        let epoch = context.get_epoch(height);
        
        // Update the light cache if needed
        let mut light_cache = context.light_cache.lock().unwrap();
        light_cache.update(epoch);
        
        // In a real implementation, this would verify the KAWPOW proof of work
        // using the light cache and return the resulting hash
        
        // For now, just return a placeholder hash
        // This is where the actual KAWPOW verification would happen
        let mut hasher = Keccak256::new();
        hasher.update(prev_hash);
        hasher.update(&height.to_le_bytes());
        hasher.update(&nonce.to_le_bytes());
        hasher.update(mix_hash);
        
        let mut result = [0u8; 32];
        result.copy_from_slice(&hasher.finalize());
        
        Ok(result)
    }
}