//! Difficulty Adjustment Algorithm for SmellyCoin
//!
//! This module implements the difficulty adjustment algorithm for SmellyCoin,
//! which aims to maintain a 15-second block time target. It uses a modified
//! version of the Ethereum difficulty adjustment algorithm with faster
//! response to hashrate changes.

use log::{debug, info, warn};
use std::cmp::{max, min};

use smellycoin_core::{Block, BlockHeader};

/// Minimum difficulty bits
const MIN_DIFFICULTY_BITS: u32 = 0x1f00ffff;

/// Maximum adjustment factor (percentage)
const MAX_ADJUSTMENT_PERCENT: u32 = 50;

/// Target block time in seconds
const TARGET_BLOCK_TIME: u64 = 15;

/// Difficulty adjustment window (number of blocks)
const DIFFICULTY_ADJUSTMENT_WINDOW: u64 = 60; // 15 minutes at 15-second blocks

/// Calculate the next difficulty bits based on the previous blocks
pub fn calculate_next_difficulty(
    prev_header: &BlockHeader,
    prev_timestamp: u32,
    blocks_since_adjustment: u64,
    average_block_time: u64,
) -> u32 {
    // Get current difficulty
    let current_difficulty = get_difficulty_for_bits(prev_header.bits);
    
    // If we're not at an adjustment interval, return the current difficulty
    if blocks_since_adjustment != 0 && blocks_since_adjustment % DIFFICULTY_ADJUSTMENT_WINDOW != 0 {
        return prev_header.bits;
    }
    
    // Calculate the adjustment factor
    let mut adjustment_factor = TARGET_BLOCK_TIME as f64 / average_block_time as f64;
    
    // Limit the adjustment factor
    let max_adjustment = 1.0 + (MAX_ADJUSTMENT_PERCENT as f64 / 100.0);
    let min_adjustment = 1.0 / max_adjustment;
    
    adjustment_factor = adjustment_factor.max(min_adjustment).min(max_adjustment);
    
    // Calculate new difficulty
    let new_difficulty = (current_difficulty as f64 * adjustment_factor) as u64;
    
    // Convert back to bits format
    let new_bits = get_bits_for_difficulty(new_difficulty);
    
    // Ensure we don't go below minimum difficulty
    let new_bits = min(new_bits, MIN_DIFFICULTY_BITS);
    
    debug!(
        "Difficulty adjustment: prev={}, new={}, factor={:.4}, avg_time={}",
        current_difficulty,
        get_difficulty_for_bits(new_bits),
        adjustment_factor,
        average_block_time
    );
    
    new_bits
}

/// Convert difficulty bits to a difficulty value
pub fn get_difficulty_for_bits(bits: u32) -> u64 {
    let exponent = ((bits >> 24) & 0xff) as u32;
    let mantissa = bits & 0x00ffffff;
    
    if exponent <= 3 {
        mantissa >> (8 * (3 - exponent))
    } else {
        mantissa << (8 * (exponent - 3))
    }
}

/// Convert a difficulty value to difficulty bits
pub fn get_bits_for_difficulty(difficulty: u64) -> u32 {
    let mut compact = 0u32;
    let mut size = difficulty;
    
    // Determine the size of the number in bytes
    let mut exponent = 0;
    while size > 0x00ffffff {
        size >>= 8;
        exponent += 1;
    }
    
    // The bits format uses a 3-byte mantissa
    let mantissa = if exponent <= 3 {
        (difficulty << (8 * (3 - exponent))) as u32
    } else {
        (difficulty >> (8 * (exponent - 3))) as u32
    };
    
    // Combine exponent and mantissa
    compact = mantissa & 0x00ffffff;
    compact |= (exponent + 3) << 24;
    
    compact
}

/// Convert difficulty bits to a target value (alias for get_target_from_bits)
pub fn bits_to_target(bits: u32) -> [u8; 32] {
    get_target_from_bits(bits)
}

/// Convert a hash to a u256 value for comparison
pub fn hash_to_u256(hash: &[u8; 32]) -> [u8; 32] {
    // For direct comparison, we just return the hash as is
    // In a more complex implementation, this might convert to a numeric representation
    *hash
}

/// Calculate the target hash from difficulty bits
pub fn get_target_from_bits(bits: u32) -> [u8; 32] {
    let mut target = [0u8; 32];
    
    let exponent = ((bits >> 24) & 0xff) as usize;
    let mantissa = bits & 0x00ffffff;
    
    // Convert mantissa to bytes (little-endian)
    target[0] = (mantissa & 0xff) as u8;
    target[1] = ((mantissa >> 8) & 0xff) as u8;
    target[2] = ((mantissa >> 16) & 0xff) as u8;
    
    // Shift according to exponent
    if exponent <= 3 {
        // Shift right
        for i in 0..29 {
            target[i] = target[i + 3 - exponent];
        }
        for i in 29..32 {
            target[i] = 0;
        }
    } else {
        // Shift left
        for i in (0..29).rev() {
            target[i + exponent - 3] = target[i];
        }
        for i in 0..(exponent - 3) {
            target[i] = 0;
        }
    }
    
    target
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_difficulty_conversion() {
        // Test difficulty bits to value and back
        let bits = 0x1d00ffff; // Bitcoin's initial difficulty
        let difficulty = get_difficulty_for_bits(bits);
        let bits_back = get_bits_for_difficulty(difficulty);
        
        assert_eq!(bits, bits_back);
    }
    
    #[test]
    fn test_target_conversion() {
        // Test difficulty bits to target
        let bits = 0x1d00ffff;
        let target = get_target_from_bits(bits);
        
        // The first few bytes should be 0xff, 0xff, 0x00, 0x00, ...
        assert_eq!(target[0], 0xff);
        assert_eq!(target[1], 0xff);
        assert_eq!(target[2], 0x00);
        
        // Most significant bytes should be zero
        for i in 3..32 {
            assert_eq!(target[i], 0);
        }
    }
    
    #[test]
    fn test_difficulty_adjustment() {
        let prev_header = BlockHeader {
            version: 1,
            prev_block_hash: [0; 32],
            merkle_root: [0; 32],
            timestamp: 1000,
            bits: 0x1d00ffff,
            nonce: 0,
        };
        
        // Test no adjustment when not at adjustment interval
        let new_bits = calculate_next_difficulty(&prev_header, 1015, 1, 15);
        assert_eq!(new_bits, prev_header.bits);
        
        // Test adjustment when blocks are too fast (10 seconds instead of 15)
        let new_bits = calculate_next_difficulty(&prev_header, 1600, DIFFICULTY_ADJUSTMENT_WINDOW, 10);
        assert!(get_difficulty_for_bits(new_bits) > get_difficulty_for_bits(prev_header.bits));
        
        // Test adjustment when blocks are too slow (20 seconds instead of 15)
        let new_bits = calculate_next_difficulty(&prev_header, 2200, DIFFICULTY_ADJUSTMENT_WINDOW, 20);
        assert!(get_difficulty_for_bits(new_bits) < get_difficulty_for_bits(prev_header.bits));
        
        // Test maximum adjustment limit
        let new_bits = calculate_next_difficulty(&prev_header, 3000, DIFFICULTY_ADJUSTMENT_WINDOW, 60);
        let adjustment = get_difficulty_for_bits(prev_header.bits) as f64 / get_difficulty_for_bits(new_bits) as f64;
        assert!(adjustment <= 1.0 + (MAX_ADJUSTMENT_PERCENT as f64 / 100.0));
    }
}