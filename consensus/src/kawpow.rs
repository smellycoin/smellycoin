//! KAWPOW Algorithm Implementation for SmellyCoin
//!
//! This module implements the KAWPOW proof-of-work algorithm, which is a
//! variant of ProgPow designed to be ASIC-resistant and GPU-friendly.
//! It is based on Ethash with modifications for improved ASIC resistance.

use bytemuck::{Pod, Zeroable};
use byteorder::{ByteOrder, LittleEndian};
use log::{debug, trace};
use once_cell::sync::Lazy;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha3::{Digest, Keccak256};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use thiserror::Error;

use smellycoin_core::{BlockHeader, Hash};
use smellycoin_util::hex;

/// KAWPOW algorithm errors
#[derive(Debug, Error)]
pub enum KawpowError {
    /// Invalid parameters
    #[error("Invalid parameters: {0}")]
    InvalidParameters(String),
    
    /// Verification error
    #[error("Verification error: {0}")]
    VerificationError(String),
    
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    
    /// Cache generation error
    #[error("Cache generation error: {0}")]
    CacheGeneration(String),
    
    /// DAG generation error
    #[error("DAG generation error: {0}")]
    DagGeneration(String),
}

/// KAWPOW algorithm parameters
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct KawpowParams {
    /// DAG cache directory
    pub cache_dir: PathBuf,
    
    /// Light cache size (in bytes)
    pub light_cache_size: usize,
    
    /// Full dataset size (in bytes)
    pub full_dataset_size: usize,
    
    /// Number of dataset accesses during mining
    pub dataset_accesses: usize,
    
    /// Period for DAG regeneration (in blocks)
    pub epoch_length: u64,
    
    /// Mix hash size (in bytes)
    pub mix_hash_size: usize,
    
    /// Hash output size (in bytes)
    pub hash_output_size: usize,
}

impl Default for KawpowParams {
    fn default() -> Self {
        KawpowParams {
            cache_dir: PathBuf::from("kawpow_cache"),
            light_cache_size: 16 * 1024 * 1024, // 16 MB
            full_dataset_size: 2 * 1024 * 1024 * 1024, // 2 GB
            dataset_accesses: 64,
            epoch_length: 7500, // ~31 hours at 15-second blocks
            mix_hash_size: 32,
            hash_output_size: 32,
        }
    }
    
    /// Get KAWPOW parameters for mainnet
    pub fn mainnet() -> Self {
        KawpowParams {
            cache_dir: PathBuf::from("kawpow_cache/mainnet"),
            light_cache_size: 16 * 1024 * 1024, // 16 MB
            full_dataset_size: 2 * 1024 * 1024 * 1024, // 2 GB
            dataset_accesses: 64,
            epoch_length: 7500, // ~31 hours at 15-second blocks
            mix_hash_size: 32,
            hash_output_size: 32,
        }
    }
    
    /// Get KAWPOW parameters for testnet
    pub fn testnet() -> Self {
        KawpowParams {
            cache_dir: PathBuf::from("kawpow_cache/testnet"),
            light_cache_size: 16 * 1024 * 1024, // 16 MB
            full_dataset_size: 2 * 1024 * 1024 * 1024, // 2 GB
            dataset_accesses: 64,
            epoch_length: 7500, // ~31 hours at 15-second blocks
            mix_hash_size: 32,
            hash_output_size: 32,
        }
    }
    
    /// Get KAWPOW parameters for regtest
    pub fn regtest() -> Self {
        KawpowParams {
            cache_dir: PathBuf::from("kawpow_cache/regtest"),
            light_cache_size: 8 * 1024 * 1024, // 8 MB (smaller for testing)
            full_dataset_size: 128 * 1024 * 1024, // 128 MB (smaller for testing)
            dataset_accesses: 32, // Fewer accesses for faster testing
            epoch_length: 100, // Shorter epochs for testing
            mix_hash_size: 32,
            hash_output_size: 32,
        }
    }
}

/// KAWPOW context for mining and verification
#[derive(Debug)]
pub struct KawpowContext {
    /// Algorithm parameters
    params: KawpowParams,
    
    /// Light cache for each epoch
    light_caches: RwLock<HashMap<u64, Arc<Vec<u8>>>>,
    
    /// Full dataset for each epoch (only used in full mining mode)
    full_datasets: RwLock<HashMap<u64, Arc<Vec<u8>>>>,
}

impl KawpowContext {
    /// Create a new KAWPOW context
    pub fn new(params: KawpowParams) -> Self {
        KawpowContext {
            params,
            light_caches: RwLock::new(HashMap::new()),
            full_datasets: RwLock::new(HashMap::new()),
        }
    }
    
    /// Get the epoch for a block height
    pub fn get_epoch(&self, height: u64) -> u64 {
        height / self.params.epoch_length
    }
    
    /// Get or generate the light cache for an epoch
    pub fn get_light_cache(&self, epoch: u64) -> Result<Arc<Vec<u8>>, KawpowError> {
        // Check if we already have the cache
        {
            let caches = self.light_caches.read().unwrap();
            if let Some(cache) = caches.get(&epoch) {
                return Ok(cache.clone());
            }
        }
        
        // Generate the cache
        let seed = self.generate_seed(epoch);
        let cache = self.generate_light_cache(&seed)?;
        let cache_arc = Arc::new(cache);
        
        // Store the cache
        {
            let mut caches = self.light_caches.write().unwrap();
            caches.insert(epoch, cache_arc.clone());
        }
        
        Ok(cache_arc)
    }
    
    /// Generate the seed for an epoch
    fn generate_seed(&self, epoch: u64) -> [u8; 32] {
        let mut seed = [0u8; 32];
        
        if epoch == 0 {
            return seed;
        }
        
        // Start with zeros for epoch 0, then use Keccak256 for subsequent epochs
        let mut hasher = Keccak256::new();
        hasher.update(seed);
        seed.copy_from_slice(&hasher.finalize());
        
        for _ in 1..epoch {
            let mut hasher = Keccak256::new();
            hasher.update(seed);
            seed.copy_from_slice(&hasher.finalize());
        }
        
        seed
    }
    
    /// Generate the light cache for a seed
    fn generate_light_cache(&self, seed: &[u8; 32]) -> Result<Vec<u8>, KawpowError> {
        let num_items = self.params.light_cache_size / 64; // 64 bytes per item
        let mut cache = vec![0u8; self.params.light_cache_size];
        
        // Generate initial item from seed
        let mut hasher = Keccak256::new();
        hasher.update(seed);
        let mut item = hasher.finalize();
        
        // Write first item to cache
        cache[0..32].copy_from_slice(&item);
        
        // Generate subsequent items
        for i in 1..num_items {
            let mut hasher = Keccak256::new();
            hasher.update(&item);
            item = hasher.finalize();
            
            let offset = i * 64;
            cache[offset..offset + 32].copy_from_slice(&item);
        }
        
        // Perform cache mixing
        for _ in 0..3 {
            for i in 0..num_items {
                let idx = LittleEndian::read_u32(&cache[i * 64..i * 64 + 4]) as usize % num_items;
                let mut hasher = Keccak256::new();
                
                let mut data = [0u8; 64];
                data[0..32].copy_from_slice(&cache[((i + num_items - 1) % num_items) * 64..((i + num_items - 1) % num_items) * 64 + 32]);
                data[32..64].copy_from_slice(&cache[idx * 64..idx * 64 + 32]);
                
                hasher.update(&data);
                let result = hasher.finalize();
                
                cache[i * 64..i * 64 + 32].copy_from_slice(&result);
            }
        }
        
        Ok(cache)
    }
    
    /// Calculate a dataset item
    fn calculate_dataset_item(&self, cache: &[u8], index: usize) -> [u8; 64] {
        let num_cache_items = cache.len() / 64;
        let r = index % num_cache_items;
        
        let mut mix = [0u8; 64];
        mix[0..32].copy_from_slice(&cache[r * 64..r * 64 + 32]);
        LittleEndian::write_u32(&mut mix[0..4], index as u32);
        
        let mut hasher = Keccak256::new();
        hasher.update(&mix[0..32]);
        let digest = hasher.finalize();
        mix[0..32].copy_from_slice(&digest);
        
        for i in 0..64 {
            let parent_index = fnv_hash(index as u32 ^ i as u32, LittleEndian::read_u32(&mix[i % 32..(i % 32) + 4])) % num_cache_items as u32;
            let parent_offset = parent_index as usize * 64;
            
            fnv_hash_merge(&mut mix, &cache[parent_offset..parent_offset + 32], i % 32);
        }
        
        let mut hasher = Keccak256::new();
        hasher.update(&mix);
        let digest = hasher.finalize();
        mix[0..32].copy_from_slice(&digest);
        
        mix
    }
    
    /// Compute the KAWPOW hash
    pub fn compute_hash(
        &self,
        header: &BlockHeader,
        nonce: u64,
        height: u64,
    ) -> Result<(Hash, Hash), KawpowError> {
        let epoch = self.get_epoch(height);
        let light_cache = self.get_light_cache(epoch)?;
        
        // Prepare the header hash
        let mut header_bytes = [0u8; 80]; // Simplified header format
        LittleEndian::write_u32(&mut header_bytes[0..4], header.version);
        header_bytes[4..36].copy_from_slice(&header.prev_block_hash);
        header_bytes[36..68].copy_from_slice(&header.merkle_root);
        LittleEndian::write_u32(&mut header_bytes[68..72], header.timestamp);
        LittleEndian::write_u32(&mut header_bytes[72..76], header.bits);
        LittleEndian::write_u64(&mut header_bytes[76..84], nonce);
        
        // Calculate the header hash
        let mut hasher = Keccak256::new();
        hasher.update(&header_bytes);
        let header_hash = hasher.finalize();
        
        // Initialize mix hash
        let mut mix_hash = [0u8; 32];
        mix_hash.copy_from_slice(&header_hash);
        
        // Perform dataset accesses
        for i in 0..self.params.dataset_accesses {
            let index = fnv_hash(i as u32, LittleEndian::read_u32(&mix_hash[i % 32..(i % 32) + 4])) % (self.params.full_dataset_size / 64) as u32;
            let item = self.calculate_dataset_item(&light_cache, index as usize);
            fnv_hash_merge(&mut mix_hash, &item, 0);
        }
        
        // Final hash
        let mut hasher = Keccak256::new();
        hasher.update(&mix_hash);
        let final_hash = hasher.finalize();
        
        let mut result_mix = [0u8; 32];
        let mut result_hash = [0u8; 32];
        result_mix.copy_from_slice(&mix_hash[0..32]);
        result_hash.copy_from_slice(&final_hash[0..32]);
        
        Ok((result_mix, result_hash))
    }
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
    let light_cache = context.get_light_cache(epoch)?;
    
    // Create a simplified block header for verification
    let mut header_bytes = [0u8; 80];
    // We don't have the full header, so we'll use what we have
    header_bytes[4..36].copy_from_slice(prev_hash);
    LittleEndian::write_u64(&mut header_bytes[76..84], nonce);
    
    // Calculate the header hash
    let mut hasher = Keccak256::new();
    hasher.update(&header_bytes);
    let header_hash = hasher.finalize();
    
    // Initialize mix hash
    let mut computed_mix = [0u8; 32];
    computed_mix.copy_from_slice(&header_hash);
    
    // Perform dataset accesses
    for i in 0..context.params.dataset_accesses {
        let index = fnv_hash(i as u32, LittleEndian::read_u32(&computed_mix[i % 32..(i % 32) + 4])) % (context.params.full_dataset_size / 64) as u32;
        let item = context.calculate_dataset_item(&light_cache, index as usize);
        fnv_hash_merge(&mut computed_mix, &item, 0);
    }
    
    // Verify that the provided mix hash matches our computed mix hash
    if mix_hash != &computed_mix[0..32] {
        return Err(KawpowError::VerificationError("Mix hash mismatch".into()));
    }
    
    // Final hash
    let mut hasher = Keccak256::new();
    hasher.update(&computed_mix);
    let final_hash = hasher.finalize();
    
    let mut result = [0u8; 32];
    result.copy_from_slice(&final_hash[0..32]);
    
    Ok(result)
}

/// FNV hash function (FNV-1a variant)
#[inline]
fn fnv_hash(v1: u32, v2: u32) -> u32 {
    const FNV_PRIME: u32 = 0x01000193;
    let mut hash = v1;
    hash ^= v2;
    hash = hash.wrapping_mul(FNV_PRIME);
    hash
}

/// FNV hash merge function
#[inline]
fn fnv_hash_merge(out: &mut [u8], input: &[u8], offset: usize) {
    for i in 0..32 {
        let idx = (offset + i) % 32;
        let v1 = LittleEndian::read_u32(&out[idx..idx + 4]);
        let v2 = LittleEndian::read_u32(&input[i..i + 4]);
        let hash = fnv_hash(v1, v2);
        LittleEndian::write_u32(&mut out[idx..idx + 4], hash);
    }
}