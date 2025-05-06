//! Network Message Protocol
//!
//! This module defines the message protocol used for communication between
//! SmellyCoin nodes, including message types, serialization, and validation.

use std::fmt;
use std::io;

use bytes::{Buf, BufMut, BytesMut};
use serde::{Deserialize, Serialize};
use thiserror::Error;

use smellycoin_core::{Block, Hash, Transaction};

/// Message protocol errors
#[derive(Debug, Error)]
pub enum MessageError {
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    
    /// Serialization error
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    
    /// Invalid message format
    #[error("Invalid message format: {0}")]
    InvalidFormat(String),
    
    /// Invalid checksum
    #[error("Invalid checksum")]
    InvalidChecksum,
    
    /// Unknown message type
    #[error("Unknown message type: {0}")]
    UnknownType(u32),
    
    /// Message too large
    #[error("Message too large: {0} bytes")]
    TooLarge(usize),
}

/// Message types
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MessageType {
    /// Version handshake
    Version = 1,
    
    /// Version acknowledgment
    VerAck = 2,
    
    /// Ping
    Ping = 3,
    
    /// Pong
    Pong = 4,
    
    /// Get address list
    GetAddr = 5,
    
    /// Address list
    Addr = 6,
    
    /// Inventory announcement
    Inv = 7,
    
    /// Get data
    GetData = 8,
    
    /// Not found
    NotFound = 9,
    
    /// Get blocks
    GetBlocks = 10,
    
    /// Get headers
    GetHeaders = 11,
    
    /// Block headers
    Headers = 12,
    
    /// Block
    Block = 13,
    
    /// Transaction
    Tx = 14,
    
    /// Memory pool request
    MemPool = 15,
    
    /// Alert
    Alert = 16,
    
    /// Reject
    Reject = 17,
    
    /// Filter load
    FilterLoad = 18,
    
    /// Filter add
    FilterAdd = 19,
    
    /// Filter clear
    FilterClear = 20,
    
    /// Merkle block
    MerkleBlock = 21,
}

impl TryFrom<u32> for MessageType {
    type Error = MessageError;
    
    fn try_from(value: u32) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(MessageType::Version),
            2 => Ok(MessageType::VerAck),
            3 => Ok(MessageType::Ping),
            4 => Ok(MessageType::Pong),
            5 => Ok(MessageType::GetAddr),
            6 => Ok(MessageType::Addr),
            7 => Ok(MessageType::Inv),
            8 => Ok(MessageType::GetData),
            9 => Ok(MessageType::NotFound),
            10 => Ok(MessageType::GetBlocks),
            11 => Ok(MessageType::GetHeaders),
            12 => Ok(MessageType::Headers),
            13 => Ok(MessageType::Block),
            14 => Ok(MessageType::Tx),
            15 => Ok(MessageType::MemPool),
            16 => Ok(MessageType::Alert),
            17 => Ok(MessageType::Reject),
            18 => Ok(MessageType::FilterLoad),
            19 => Ok(MessageType::FilterAdd),
            20 => Ok(MessageType::FilterClear),
            21 => Ok(MessageType::MerkleBlock),
            _ => Err(MessageError::UnknownType(value)),
        }
    }
}

impl fmt::Display for MessageType {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            MessageType::Version => write!(f, "version"),
            MessageType::VerAck => write!(f, "verack"),
            MessageType::Ping => write!(f, "ping"),
            MessageType::Pong => write!(f, "pong"),
            MessageType::GetAddr => write!(f, "getaddr"),
            MessageType::Addr => write!(f, "addr"),
            MessageType::Inv => write!(f, "inv"),
            MessageType::GetData => write!(f, "getdata"),
            MessageType::NotFound => write!(f, "notfound"),
            MessageType::GetBlocks => write!(f, "getblocks"),
            MessageType::GetHeaders => write!(f, "getheaders"),
            MessageType::Headers => write!(f, "headers"),
            MessageType::Block => write!(f, "block"),
            MessageType::Tx => write!(f, "tx"),
            MessageType::MemPool => write!(f, "mempool"),
            MessageType::Alert => write!(f, "alert"),
            MessageType::Reject => write!(f, "reject"),
            MessageType::FilterLoad => write!(f, "filterload"),
            MessageType::FilterAdd => write!(f, "filteradd"),
            MessageType::FilterClear => write!(f, "filterclear"),
            MessageType::MerkleBlock => write!(f, "merkleblock"),
        }
    }
}

/// Network message
#[derive(Debug, Clone)]
pub struct Message {
    /// Message type
    pub msg_type: MessageType,
    
    /// Message payload
    pub payload: Vec<u8>,
}

impl Message {
    /// Maximum message size (4 MB)
    pub const MAX_SIZE: usize = 4 * 1024 * 1024;
    
    /// Create a new message
    pub fn new(msg_type: MessageType, payload: Vec<u8>) -> Self {
        Message {
            msg_type,
            payload,
        }
    }
    
    /// Serialize the message to bytes
    pub fn serialize(&self) -> Result<Vec<u8>, MessageError> {
        let mut buffer = BytesMut::with_capacity(self.payload.len() + 8);
        
        // Write message type
        buffer.put_u32(self.msg_type as u32);
        
        // Write payload length
        buffer.put_u32(self.payload.len() as u32);
        
        // Write payload
        buffer.extend_from_slice(&self.payload);
        
        // Calculate checksum
        let checksum = calculate_checksum(&buffer);
        
        // Create final message with magic and checksum
        let mut message = BytesMut::with_capacity(buffer.len() + 8);
        message.put_u32(NETWORK_MAGIC); // Network magic
        message.extend_from_slice(&buffer);
        message.put_u32(checksum);
        
        Ok(message.freeze().to_vec())
    }
    
    /// Deserialize a message from bytes
    pub fn deserialize(bytes: &[u8]) -> Result<Self, MessageError> {
        if bytes.len() < 16 {
            return Err(MessageError::InvalidFormat("Message too short".to_string()));
        }
        
        let mut cursor = io::Cursor::new(bytes);
        
        // Read and verify network magic
        let magic = cursor.get_u32();
        if magic != NETWORK_MAGIC {
            return Err(MessageError::InvalidFormat(format!("Invalid network magic: {:x}", magic)));
        }
        
        // Read message type
        let msg_type_value = cursor.get_u32();
        let msg_type = MessageType::try_from(msg_type_value)?;
        
        // Read payload length
        let payload_len = cursor.get_u32() as usize;
        if payload_len > Self::MAX_SIZE {
            return Err(MessageError::TooLarge(payload_len));
        }
        
        // Verify message length
        if bytes.len() < 16 + payload_len {
            return Err(MessageError::InvalidFormat("Incomplete message".to_string()));
        }
        
        // Verify checksum
        let message_data = &bytes[4..(12 + payload_len)];
        let expected_checksum = calculate_checksum(message_data);
        let actual_checksum = cursor.get_u32_at(12 + payload_len);
        
        if expected_checksum != actual_checksum {
            return Err(MessageError::InvalidChecksum);
        }
        
        // Extract payload
        let payload = bytes[12..(12 + payload_len)].to_vec();
        
        Ok(Message {
            msg_type,
            payload,
        })
    }
}

/// Network magic value (unique to SmellyCoin)
/// This distinguishes SmellyCoin messages from other protocols
const NETWORK_MAGIC: u32 = 0x5343_4F49_4E; // "SCOIN" in ASCII

/// Calculate a checksum for a message
fn calculate_checksum(data: &[u8]) -> u32 {
    // In a real implementation, this would be a proper checksum algorithm
    // For simplicity, we'll use a simple CRC32-like algorithm
    let mut checksum: u32 = 0xFFFF_FFFF;
    
    for &byte in data {
        checksum ^= byte as u32;
        for _ in 0..8 {
            checksum = if checksum & 1 == 1 {
                (checksum >> 1) ^ 0xEDB8_8320
            } else {
                checksum >> 1
            };
        }
    }
    
    !checksum
}

/// Trait for message payload serialization
pub trait MessagePayload: Sized {
    /// Serialize the payload to bytes
    fn serialize(&self) -> Result<Vec<u8>, MessageError>;
    
    /// Deserialize the payload from bytes
    fn deserialize(bytes: &[u8]) -> Result<Self, MessageError>;
    
    /// Get the message type
    fn message_type() -> MessageType;
    
    /// Create a message from this payload
    fn into_message(&self) -> Result<Message, MessageError> {
        let payload = self.serialize()?;
        Ok(Message::new(Self::message_type(), payload))
    }
}

/// Version message payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VersionPayload {
    /// Protocol version
    pub version: u32,
    
    /// Services offered
    pub services: u64,
    
    /// Timestamp
    pub timestamp: i64,
    
    /// Receiving node address
    pub addr_recv: String,
    
    /// Transmitting node address
    pub addr_from: String,
    
    /// Nonce to detect connections to self
    pub nonce: u64,
    
    /// User agent string
    pub user_agent: String,
    
    /// Last block height
    pub start_height: u32,
    
    /// Relay transactions flag
    pub relay: bool,
}

impl MessagePayload for VersionPayload {
    fn serialize(&self) -> Result<Vec<u8>, MessageError> {
        serde_json::to_vec(self).map_err(MessageError::Serialization)
    }
    
    fn deserialize(bytes: &[u8]) -> Result<Self, MessageError> {
        serde_json::from_slice(bytes).map_err(MessageError::Serialization)
    }
    
    fn message_type() -> MessageType {
        MessageType::Version
    }
}

/// Inventory types
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum InvType {
    /// Error
    Error = 0,
    
    /// Transaction
    Tx = 1,
    
    /// Block
    Block = 2,
    
    /// Filtered block
    FilteredBlock = 3,
    
    /// Compact block
    CompactBlock = 4,
}

/// Inventory vector
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvVec {
    /// Type
    pub inv_type: InvType,
    
    /// Hash
    pub hash: Hash,
}

/// Inventory message payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvPayload {
    /// Inventory vectors
    pub inventory: Vec<InvVec>,
}

impl MessagePayload for InvPayload {
    fn serialize(&self) -> Result<Vec<u8>, MessageError> {
        serde_json::to_vec(self).map_err(MessageError::Serialization)
    }
    
    fn deserialize(bytes: &[u8]) -> Result<Self, MessageError> {
        serde_json::from_slice(bytes).map_err(MessageError::Serialization)
    }
    
    fn message_type() -> MessageType {
        MessageType::Inv
    }
}

/// Get blocks message payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GetBlocksPayload {
    /// Protocol version
    pub version: u32,
    
    /// Block locator hashes
    pub block_locator_hashes: Vec<Hash>,
    
    /// Hash of the last desired block
    pub hash_stop: Hash,
}

impl MessagePayload for GetBlocksPayload {
    fn serialize(&self) -> Result<Vec<u8>, MessageError> {
        serde_json::to_vec(self).map_err(MessageError::Serialization)
    }
    
    fn deserialize(bytes: &[u8]) -> Result<Self, MessageError> {
        serde_json::from_slice(bytes).map_err(MessageError::Serialization)
    }
    
    fn message_type() -> MessageType {
        MessageType::GetBlocks
    }
}

/// Get headers message payload
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GetHeadersPayload {
    /// Protocol version
    pub version: u32,
    
    /// Block locator hashes
    pub block_locator_hashes: Vec<Hash>,
    
    /// Hash of the last desired header
    pub hash_stop: Hash,
}

impl MessagePayload for GetHeadersPayload {
    fn serialize(&self) -> Result<Vec<u8>, MessageError> {
        serde_json::to_vec(self).map_err(MessageError::Serialization)
    }
    
    fn deserialize(bytes: &[u8]) -> Result<Self, MessageError> {
        serde_json::from_slice(bytes).map_err(MessageError::Serialization)
    }
    
    fn message_type() -> MessageType {
        MessageType::GetHeaders
    }
}