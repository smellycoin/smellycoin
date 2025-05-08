//! Utility functions and types for SmellyCoin

// No need for fmt import
use thiserror::Error;

#[derive(Error, Debug)]
pub enum UtilError {
    #[error("Hex decoding error: {0}")]
    HexError(#[from] hex::FromHexError),
    
    #[error("Base58 decoding error: {0}")]
    Base58Error(#[from] bs58::decode::Error),
    
    #[error("Invalid format: {0}")]
    InvalidFormat(String),
}

/// Converts a hex string to bytes
pub fn hex_to_bytes(hex: &str) -> Result<Vec<u8>, UtilError> {
    Ok(hex::decode(hex)?)
}

/// Converts bytes to a hex string
pub fn bytes_to_hex(bytes: &[u8]) -> String {
    hex::encode(bytes)
}

/// Converts a base58 string to bytes
pub fn base58_to_bytes(b58: &str) -> Result<Vec<u8>, UtilError> {
    Ok(bs58::decode(b58).into_vec()?)
}

/// Converts bytes to a base58 string
pub fn bytes_to_base58(bytes: &[u8]) -> String {
    bs58::encode(bytes).into_string()
}

/// Formats a timestamp as an ISO 8601 string
pub fn format_timestamp(timestamp: i64) -> String {
    chrono::DateTime::<chrono::Utc>::from_timestamp(timestamp, 0)
        .map(|dt| dt.format("%Y-%m-%dT%H:%M:%SZ").to_string())
        .unwrap_or_else(|| "Invalid timestamp".to_string())
}