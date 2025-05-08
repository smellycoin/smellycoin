//! Transaction Implementation for SmellyCoin
//!
//! This module defines the transaction structures for SmellyCoin,
//! including inputs, outputs, and validation logic.

use serde::{Deserialize, Serialize};
use std::fmt;
use thiserror::Error;

use crate::Hash;

/// Transaction validation errors
#[derive(Debug, Error)]
pub enum TransactionValidationError {
    /// No inputs
    #[error("Transaction has no inputs")]
    NoInputs,
    
    /// No outputs
    #[error("Transaction has no outputs")]
    NoOutputs,
    
    /// Input not found
    #[error("Input not found: {0}")]
    InputNotFound(String),
    
    /// Double spend attempt
    #[error("Double spend attempt: {0}")]
    DoubleSpend(String),
    
    /// Invalid signature
    #[error("Invalid signature: {0}")]
    InvalidSignature(String),
    
    /// Coinbase maturity
    #[error("Coinbase maturity: {0}")]
    CoinbaseMaturity(String),
    
    /// Insufficient funds
    #[error("Insufficient funds: {0}")]
    InsufficientFunds(String),
    
    /// Fee too low
    #[error("Fee too low: {0}")]
    FeeTooLow(String),
    
    /// Transaction too large
    #[error("Transaction too large: {0}")]
    TooLarge(String),
}

/// Transaction input
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionInput {
    /// Previous transaction ID
    pub prev_txid: Hash,
    
    /// Previous transaction output index
    pub prev_vout: u32,
    
    /// Script signature
    pub script_sig: Vec<u8>,
    
    /// Sequence number
    pub sequence: u32,
}

impl TransactionInput {
    /// Create a new transaction input
    pub fn new(
        prev_txid: Hash,
        prev_vout: u32,
        script_sig: Vec<u8>,
        sequence: u32,
    ) -> Self {
        TransactionInput {
            prev_txid,
            prev_vout,
            script_sig,
            sequence,
        }
    }
    
    /// Create a coinbase input
    pub fn coinbase(height: u64, extra_nonce: u32) -> Self {
        let mut script_sig = Vec::with_capacity(12);
        
        // Height (BIP-34)
        let height_bytes = height.to_le_bytes();
        script_sig.push(height_bytes.len() as u8);
        script_sig.extend_from_slice(&height_bytes);
        
        // Extra nonce
        let extra_nonce_bytes = extra_nonce.to_le_bytes();
        script_sig.push(extra_nonce_bytes.len() as u8);
        script_sig.extend_from_slice(&extra_nonce_bytes);
        
        // Arbitrary data (e.g., mining pool identifier)
        script_sig.extend_from_slice(b"SmellyCoin");
        
        TransactionInput {
            prev_txid: [0; 32], // Coinbase has all zeros
            prev_vout: 0xffffffff, // Coinbase uses max value
            script_sig,
            sequence: 0xffffffff, // Max sequence
        }
    }
    
    /// Check if this is a coinbase input
    pub fn is_coinbase(&self) -> bool {
        self.prev_txid == [0; 32] && self.prev_vout == 0xffffffff
    }
}

impl fmt::Display for TransactionInput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.is_coinbase() {
            write!(f, "Coinbase({})", hex::encode(&self.script_sig[0..4]))
        } else {
            write!(
                f,
                "Input({}:{}, sig: {} bytes)",
                hex::encode(&self.prev_txid[0..4]),
                self.prev_vout,
                self.script_sig.len()
            )
        }
    }
}

/// Transaction output
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransactionOutput {
    /// Output value in smallest units (satoshis)
    pub value: u64,
    
    /// Script public key
    pub script_pubkey: Vec<u8>,
}

impl TransactionOutput {
    /// Create a new transaction output
    pub fn new(value: u64, script_pubkey: Vec<u8>) -> Self {
        TransactionOutput {
            value,
            script_pubkey,
        }
    }
    
    /// Create a P2PKH (Pay to Public Key Hash) output
    pub fn p2pkh(value: u64, pubkey_hash: &[u8; 20]) -> Self {
        // P2PKH script: OP_DUP OP_HASH160 <pubKeyHash> OP_EQUALVERIFY OP_CHECKSIG
        let mut script = Vec::with_capacity(25);
        script.push(0x76); // OP_DUP
        script.push(0xa9); // OP_HASH160
        script.push(0x14); // Push 20 bytes
        script.extend_from_slice(pubkey_hash);
        script.push(0x88); // OP_EQUALVERIFY
        script.push(0xac); // OP_CHECKSIG
        
        TransactionOutput {
            value,
            script_pubkey: script,
        }
    }
    
    /// Get the address from this output (if it's a standard P2PKH)
    pub fn get_address(&self) -> Option<[u8; 20]> {
        // Check if this is a P2PKH script
        if self.script_pubkey.len() == 25 &&
           self.script_pubkey[0] == 0x76 && // OP_DUP
           self.script_pubkey[1] == 0xa9 && // OP_HASH160
           self.script_pubkey[2] == 0x14 && // Push 20 bytes
           self.script_pubkey[23] == 0x88 && // OP_EQUALVERIFY
           self.script_pubkey[24] == 0xac { // OP_CHECKSIG
            
            let mut pubkey_hash = [0u8; 20];
            pubkey_hash.copy_from_slice(&self.script_pubkey[3..23]);
            Some(pubkey_hash)
        } else {
            None
        }
    }
}

impl fmt::Display for TransactionOutput {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Output({} satoshis, script: {} bytes)",
            self.value,
            self.script_pubkey.len()
        )
    }
}

/// Transaction
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Transaction {
    /// Transaction version
    pub version: u32,
    
    /// Transaction inputs
    pub inputs: Vec<TransactionInput>,
    
    /// Transaction outputs
    pub outputs: Vec<TransactionOutput>,
    
    /// Lock time
    pub lock_time: u32,
    
    /// Transaction ID (hash of the transaction)
    pub txid: Hash,
}

impl Transaction {
    /// Create a new transaction
    pub fn new(
        version: u32,
        inputs: Vec<TransactionInput>,
        outputs: Vec<TransactionOutput>,
        lock_time: u32,
    ) -> Self {
        let mut tx = Transaction {
            version,
            inputs,
            outputs,
            lock_time,
            txid: [0; 32], // Will be calculated
        };
        
        // Calculate the transaction ID
        tx.txid = tx.calculate_txid();
        tx
    }
    
    /// Create a coinbase transaction
    pub fn coinbase(height: u64, reward: u64, address: &[u8; 20], extra_nonce: u32) -> Self {
        let input = TransactionInput::coinbase(height, extra_nonce);
        let output = TransactionOutput::p2pkh(reward, address);
        
        let mut tx = Transaction {
            version: 1,
            inputs: vec![input],
            outputs: vec![output],
            lock_time: 0,
            txid: [0; 32], // Will be calculated
        };
        
        // Calculate the transaction ID
        tx.txid = tx.calculate_txid();
        
        tx
    }
    
    /// Calculate the transaction ID
    pub fn calculate_txid(&self) -> Hash {
        // In a real implementation, this would calculate the double SHA-256
        // hash of the serialized transaction
        // For simplicity, this is a placeholder
        let mut hash = [0; 32];
        hash[0] = (self.version & 0xff) as u8;
        hash[1] = ((self.version >> 8) & 0xff) as u8;
        hash[2] = self.inputs.len() as u8;
        hash[3] = self.outputs.len() as u8;
        hash
    }
    
    /// Check if this is a coinbase transaction
    pub fn is_coinbase(&self) -> bool {
        self.inputs.len() == 1 && self.inputs[0].is_coinbase()
    }
    
    /// Get the total output value
    pub fn get_output_value(&self) -> u64 {
        self.outputs.iter().map(|output| output.value).sum()
    }
    
    /// Get the total input value (requires UTXO set)
    pub fn get_input_value<F>(&self, get_output_value: F) -> Result<u64, TransactionValidationError>
    where
        F: Fn(&Hash, u32) -> Option<u64>,
    {
        if self.is_coinbase() {
            return Ok(0); // Coinbase has no input value
        }
        
        let mut total = 0;
        
        for input in &self.inputs {
            let value = get_output_value(&input.prev_txid, input.prev_vout)
                .ok_or_else(|| {
                    TransactionValidationError::InputNotFound(
                        format!("{}:{}", hex::encode(input.prev_txid), input.prev_vout)
                    )
                })?;
                
            total += value;
        }
        
        Ok(total)
    }
    
    /// Get the transaction fee (requires UTXO set)
    pub fn get_fee<F>(&self, get_output_value: F) -> Result<u64, TransactionValidationError>
    where
        F: Fn(&Hash, u32) -> Option<u64>,
    {
        if self.is_coinbase() {
            return Ok(0); // Coinbase has no fee
        }
        
        let input_value = self.get_input_value(get_output_value)?;
        let output_value = self.get_output_value();
        
        if input_value < output_value {
            return Err(TransactionValidationError::InsufficientFunds(
                format!("Input: {}, Output: {}", input_value, output_value)
            ));
        }
        
        Ok(input_value - output_value)
    }
    
    /// Validate the transaction (basic checks)
    pub fn validate_basic(&self) -> Result<(), TransactionValidationError> {
        // Check inputs
        if self.inputs.is_empty() {
            return Err(TransactionValidationError::NoInputs);
        }
        
        // Check outputs
        if self.outputs.is_empty() {
            return Err(TransactionValidationError::NoOutputs);
        }
        
        // Check output values
        for output in &self.outputs {
            if output.value == 0 {
                return Err(TransactionValidationError::InsufficientFunds(
                    "Output value is zero".to_string()
                ));
            }
        }
        
        // Additional checks would be done here in a full implementation
        
        Ok(())
    }
    
    /// Get the transaction hash (same as txid)
    pub fn hash(&self) -> Hash {
        self.txid
    }
    
    /// Validate the transaction (full validation with UTXO set)
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
        self.validate_basic()?;
        
        // Skip further validation for coinbase
        if self.is_coinbase() {
            return Ok(());
        }
        
        // Check size
        let estimated_size = 10 + (self.inputs.len() * 150) + (self.outputs.len() * 34);
        if estimated_size > max_size {
            return Err(TransactionValidationError::TooLarge(
                format!("Size: {} bytes, Max: {} bytes", estimated_size, max_size)
            ));
        }
        
        // Check fee
        let fee = self.get_fee(&get_output_value)?;
        let min_fee = (estimated_size as u64 * min_fee_rate) / 1000; // satoshis per KB
        
        if fee < min_fee {
            return Err(TransactionValidationError::FeeTooLow(
                format!("Fee: {}, Min: {}", fee, min_fee)
            ));
        }
        
        // Verify signatures
        for input in &self.inputs {
            if !verify_signature(input, &self.txid) {
                return Err(TransactionValidationError::InvalidSignature(
                    format!("Invalid signature for input {}:{}", 
                            hex::encode(input.prev_txid), input.prev_vout)
                ));
            }
        }
        
        Ok(())
    }
}

impl fmt::Display for Transaction {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Tx {{ id: {}, ins: {}, outs: {} }}",
            hex::encode(&self.txid[0..8]),
            self.inputs.len(),
            self.outputs.len()
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_transaction_creation() {
        // Create a transaction
        let mut prev_txid = [0; 32];
        prev_txid[0] = 1;
        
        let input = TransactionInput::new(
            prev_txid,
            0,
            vec![0x30, 0x45, 0x02, 0x20], // Fake signature
            0xffffffff,
        );
        
        let pubkey_hash = [0; 20];
        let output = TransactionOutput::p2pkh(50_000_000, &pubkey_hash); // 0.5 SMC
        
        let tx = Transaction::new(
            1,
            vec![input],
            vec![output],
            0,
        );
        
        // Verify transaction
        assert_eq!(tx.version, 1);
        assert_eq!(tx.inputs.len(), 1);
        assert_eq!(tx.outputs.len(), 1);
        assert_eq!(tx.lock_time, 0);
        assert_ne!(tx.txid, [0; 32]); // TXID should be calculated
        
        // Verify input
        assert_eq!(tx.inputs[0].prev_txid, prev_txid);
        assert_eq!(tx.inputs[0].prev_vout, 0);
        assert_eq!(tx.inputs[0].sequence, 0xffffffff);
        
        // Verify output
        assert_eq!(tx.outputs[0].value, 50_000_000);
        assert_eq!(tx.outputs[0].script_pubkey.len(), 25); // P2PKH script length
        
        // Verify not coinbase
        assert!(!tx.is_coinbase());
    }
    
    #[test]
    fn test_coinbase_transaction() {
        let address = [0; 20];
        let tx = Transaction::coinbase(12345, 50_000_000, &address, 42);
        
        // Verify transaction
        assert_eq!(tx.version, 1);
        assert_eq!(tx.inputs.len(), 1);
        assert_eq!(tx.outputs.len(), 1);
        assert_eq!(tx.lock_time, 0);
        
        // Verify coinbase input
        assert!(tx.inputs[0].is_coinbase());
        assert!(tx.is_coinbase());
        
        // Verify output
        assert_eq!(tx.outputs[0].value, 50_000_000);
        
        // Verify output address
        let output_address = tx.outputs[0].get_address().unwrap();
        assert_eq!(output_address, address);
    }
    
    #[test]
    fn test_transaction_validation() {
        // Create a transaction
        let mut prev_txid = [0; 32];
        prev_txid[0] = 1;
        
        let input = TransactionInput::new(
            prev_txid,
            0,
            vec![0x30, 0x45, 0x02, 0x20], // Fake signature
            0xffffffff,
        );
        
        let pubkey_hash = [0; 20];
        let output = TransactionOutput::p2pkh(50_000_000, &pubkey_hash); // 0.5 SMC
        
        let tx = Transaction::new(
            1,
            vec![input],
            vec![output],
            0,
        );
        
        // Basic validation
        assert!(tx.validate_basic().is_ok());
        
        // Create a transaction with no inputs
        let invalid_tx = Transaction::new(
            1,
            vec![],
            vec![output],
            0,
        );
        
        // Should fail validation
        assert!(matches!(invalid_tx.validate_basic(), Err(TransactionValidationError::NoInputs)));
        
        // Create a transaction with no outputs
        let invalid_tx = Transaction::new(
            1,
            vec![input],
            vec![],
            0,
        );
        
        // Should fail validation
        assert!(matches!(invalid_tx.validate_basic(), Err(TransactionValidationError::NoOutputs)));
    }
}