//! UTXO Set Management for SmellyCoin
//!
//! This module implements the Unspent Transaction Output (UTXO) set management
//! for SmellyCoin. It provides efficient storage and retrieval of UTXOs,
//! as well as state pruning capabilities to optimize resource usage.

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, RwLock};
use thiserror::Error;

use crate::{Hash, Transaction, TransactionInput, TransactionOutput};

/// UTXO-related errors
#[derive(Debug, Error)]
pub enum UTXOError {
    /// UTXO not found
    #[error("UTXO not found: {0}")]
    NotFound(String),
    
    /// Double spend attempt
    #[error("Double spend attempt: {0}")]
    DoubleSpend(String),
    
    /// Invalid transaction
    #[error("Invalid transaction: {0}")]
    InvalidTransaction(String),
    
    /// Storage error
    #[error("Storage error: {0}")]
    Storage(String),
}

/// UTXO entry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UTXOEntry {
    /// Transaction ID
    pub txid: Hash,
    
    /// Output index
    pub vout: u32,
    
    /// Output value
    pub value: u64,
    
    /// Output script (serialized)
    pub script_pubkey: Vec<u8>,
    
    /// Block height where this UTXO was created
    pub height: u64,
    
    /// Whether this output is coinbase
    pub is_coinbase: bool,
}

/// UTXO set
#[derive(Debug)]
pub struct UTXOSet {
    /// UTXO entries by outpoint (txid + vout)
    utxos: RwLock<HashMap<(Hash, u32), UTXOEntry>>,
    
    /// Pruning height (UTXOs below this height may be pruned)
    pruning_height: RwLock<u64>,
    
    /// Pruning enabled flag
    pruning_enabled: bool,
}

impl UTXOSet {
    /// Create a new UTXO set
    pub fn new(pruning_enabled: bool) -> Self {
        UTXOSet {
            utxos: RwLock::new(HashMap::new()),
            pruning_height: RwLock::new(0),
            pruning_enabled,
        }
    }
    
    /// Get a UTXO entry
    pub fn get(&self, txid: &Hash, vout: u32) -> Option<UTXOEntry> {
        let utxos = self.utxos.read().unwrap();
        utxos.get(&(*txid, vout)).cloned()
    }
    
    /// Check if a UTXO exists
    pub fn exists(&self, txid: &Hash, vout: u32) -> bool {
        let utxos = self.utxos.read().unwrap();
        utxos.contains_key(&(*txid, vout))
    }
    
    /// Add a UTXO entry
    pub fn add(&self, entry: UTXOEntry) -> Result<(), UTXOError> {
        let mut utxos = self.utxos.write().unwrap();
        let key = (entry.txid, entry.vout);
        
        if utxos.contains_key(&key) {
            return Err(UTXOError::DoubleSpend(
                format!("UTXO already exists: {}:{}", hex::encode(entry.txid), entry.vout)
            ));
        }
        
        utxos.insert(key, entry);
        Ok(())
    }
    
    /// Remove a UTXO entry
    pub fn remove(&self, txid: &Hash, vout: u32) -> Result<UTXOEntry, UTXOError> {
        let mut utxos = self.utxos.write().unwrap();
        let key = (*txid, vout);
        
        if let Some(entry) = utxos.remove(&key) {
            Ok(entry)
        } else {
            Err(UTXOError::NotFound(
                format!("UTXO not found: {}:{}", hex::encode(*txid), vout)
            ))
        }
    }
    
    /// Process a transaction (add outputs, remove inputs)
    pub fn process_transaction(
        &self,
        tx: &Transaction,
        height: u64,
        is_coinbase: bool,
    ) -> Result<(), UTXOError> {
        // First, verify and remove all inputs (except for coinbase)
        if !is_coinbase {
            for input in &tx.inputs {
                self.remove(&input.prev_txid, input.prev_vout)?;
            }
        }
        
        // Then add all outputs
        for (vout, output) in tx.outputs.iter().enumerate() {
            let entry = UTXOEntry {
                txid: tx.txid,
                vout: vout as u32,
                value: output.value,
                script_pubkey: output.script_pubkey.clone(),
                height,
                is_coinbase,
            };
            
            self.add(entry)?;
        }
        
        Ok(())
    }
    
    /// Process a block (add all transaction outputs, remove all inputs)
    pub fn process_block(
        &self,
        txs: &[Transaction],
        height: u64,
    ) -> Result<(), UTXOError> {
        // Process coinbase transaction first
        if !txs.is_empty() {
            self.process_transaction(&txs[0], height, true)?;
        }
        
        // Process remaining transactions
        for tx in txs.iter().skip(1) {
            self.process_transaction(tx, height, false)?;
        }
        
        Ok(())
    }
    
    /// Revert a transaction (remove outputs, add inputs)
    pub fn revert_transaction(
        &self,
        tx: &Transaction,
        height: u64,
        is_coinbase: bool,
    ) -> Result<(), UTXOError> {
        // First, remove all outputs
        for vout in 0..tx.outputs.len() {
            self.remove(&tx.txid, vout as u32)?;
        }
        
        // Then add back all inputs (except for coinbase)
        if !is_coinbase {
            // Note: This requires access to the previous transaction outputs
            // In a real implementation, we would need to store or retrieve this information
            // For simplicity, this is left as a placeholder
        }
        
        Ok(())
    }
    
    /// Set the pruning height
    pub fn set_pruning_height(&self, height: u64) {
        let mut pruning_height = self.pruning_height.write().unwrap();
        *pruning_height = height;
    }
    
    /// Prune UTXOs below the pruning height
    pub fn prune(&self) -> Result<usize, UTXOError> {
        if !self.pruning_enabled {
            return Ok(0);
        }
        
        let pruning_height = *self.pruning_height.read().unwrap();
        let mut utxos = self.utxos.write().unwrap();
        
        let to_remove: Vec<(Hash, u32)> = utxos
            .iter()
            .filter(|(_, entry)| entry.height < pruning_height && !entry.is_coinbase)
            .map(|(key, _)| key.clone())
            .collect();
        
        let count = to_remove.len();
        for key in to_remove {
            utxos.remove(&key);
        }
        
        Ok(count)
    }
    
    /// Get the total number of UTXOs
    pub fn len(&self) -> usize {
        let utxos = self.utxos.read().unwrap();
        utxos.len()
    }
    
    /// Check if the UTXO set is empty
    pub fn is_empty(&self) -> bool {
        let utxos = self.utxos.read().unwrap();
        utxos.is_empty()
    }
    
    /// Calculate the total value in the UTXO set
    pub fn total_value(&self) -> u64 {
        let utxos = self.utxos.read().unwrap();
        utxos.values().map(|entry| entry.value).sum()
    }
    
    /// Get all UTXOs for an address
    pub fn get_for_address(&self, address_hash: &[u8]) -> Vec<UTXOEntry> {
        let utxos = self.utxos.read().unwrap();
        
        utxos
            .values()
            .filter(|entry| {
                // In a real implementation, we would check if the script_pubkey
                // corresponds to the given address hash
                // For simplicity, this is a placeholder
                !entry.script_pubkey.is_empty() && entry.script_pubkey[0] == address_hash[0]
            })
            .cloned()
            .collect()
    }
    
    /// Get the best UTXOs for a given amount
    pub fn select_utxos(
        &self,
        address_hash: &[u8],
        amount: u64,
        fee_rate: u64,
    ) -> Result<(Vec<UTXOEntry>, u64), UTXOError> {
        let utxos = self.get_for_address(address_hash);
        
        if utxos.is_empty() {
            return Err(UTXOError::NotFound(
                format!("No UTXOs found for address: {}", hex::encode(address_hash))
            ));
        }
        
        // Sort UTXOs by value (ascending)
        let mut sorted_utxos = utxos;
        sorted_utxos.sort_by_key(|entry| entry.value);
        
        // Try to find a single UTXO that covers the amount
        if let Some(entry) = sorted_utxos.iter().find(|entry| entry.value >= amount) {
            return Ok((vec![entry.clone()], entry.value - amount));
        }
        
        // Otherwise, use multiple UTXOs
        let mut selected = Vec::new();
        let mut total = 0;
        
        for entry in sorted_utxos {
            selected.push(entry.clone());
            total += entry.value;
            
            if total >= amount {
                return Ok((selected, total - amount));
            }
        }
        
        Err(UTXOError::NotFound(
            format!("Insufficient funds: have {}, need {}", total, amount)
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    // Helper function to create a test transaction
    fn create_test_tx(inputs: Vec<(Hash, u32)>, output_values: Vec<u64>) -> Transaction {
        let mut tx = Transaction {
            version: 1,
            inputs: Vec::new(),
            outputs: Vec::new(),
            lock_time: 0,
            txid: [0; 32], // Will be calculated
        };
        
        // Add inputs
        for (prev_txid, prev_vout) in inputs {
            tx.inputs.push(TransactionInput {
                prev_txid,
                prev_vout,
                script_sig: vec![],
                sequence: 0xffffffff,
            });
        }
        
        // Add outputs
        for value in output_values {
            tx.outputs.push(TransactionOutput {
                value,
                script_pubkey: vec![0x76, 0xa9, 0x14], // Simple script
            });
        }
        
        // Calculate txid (simplified for testing)
        let mut txid = [0; 32];
        txid[0] = tx.inputs.len() as u8;
        txid[1] = tx.outputs.len() as u8;
        tx.txid = txid;
        
        tx
    }
    
    #[test]
    fn test_utxo_add_remove() {
        let utxo_set = UTXOSet::new(false);
        
        // Create a UTXO entry
        let mut txid = [0; 32];
        txid[0] = 1;
        
        let entry = UTXOEntry {
            txid,
            vout: 0,
            value: 100,
            script_pubkey: vec![0x76, 0xa9, 0x14],
            height: 1,
            is_coinbase: true,
        };
        
        // Add the entry
        assert!(utxo_set.add(entry.clone()).is_ok());
        
        // Verify it exists
        assert!(utxo_set.exists(&txid, 0));
        
        // Get the entry
        let retrieved = utxo_set.get(&txid, 0).unwrap();
        assert_eq!(retrieved.value, 100);
        
        // Remove the entry
        let removed = utxo_set.remove(&txid, 0).unwrap();
        assert_eq!(removed.value, 100);
        
        // Verify it no longer exists
        assert!(!utxo_set.exists(&txid, 0));
    }
    
    #[test]
    fn test_process_transaction() {
        let utxo_set = UTXOSet::new(false);
        
        // Create a coinbase transaction
        let coinbase_tx = create_test_tx(vec![], vec![50]);
        
        // Process the coinbase transaction
        assert!(utxo_set.process_transaction(&coinbase_tx, 1, true).is_ok());
        
        // Verify the UTXO was created
        assert!(utxo_set.exists(&coinbase_tx.txid, 0));
        
        // Create a spending transaction
        let spending_tx = create_test_tx(
            vec![(coinbase_tx.txid, 0)],
            vec![30, 20], // Split into two outputs
        );
        
        // Process the spending transaction
        assert!(utxo_set.process_transaction(&spending_tx, 2, false).is_ok());
        
        // Verify the original UTXO was spent
        assert!(!utxo_set.exists(&coinbase_tx.txid, 0));
        
        // Verify the new UTXOs were created
        assert!(utxo_set.exists(&spending_tx.txid, 0));
        assert!(utxo_set.exists(&spending_tx.txid, 1));
        
        // Verify the values
        assert_eq!(utxo_set.get(&spending_tx.txid, 0).unwrap().value, 30);
        assert_eq!(utxo_set.get(&spending_tx.txid, 1).unwrap().value, 20);
    }
    
    #[test]
    fn test_pruning() {
        let utxo_set = UTXOSet::new(true);
        
        // Create and add some UTXOs at different heights
        for i in 0..10 {
            let mut txid = [0; 32];
            txid[0] = i as u8;
            
            let entry = UTXOEntry {
                txid,
                vout: 0,
                value: 100,
                script_pubkey: vec![0x76, 0xa9, 0x14],
                height: i,
                is_coinbase: i == 0, // Only the first one is coinbase
            };
            
            assert!(utxo_set.add(entry).is_ok());
        }
        
        // Set pruning height to 5
        utxo_set.set_pruning_height(5);
        
        // Prune UTXOs
        let pruned = utxo_set.prune().unwrap();
        
        // We should have pruned 4 UTXOs (heights 1-4, not 0 because it's coinbase)
        assert_eq!(pruned, 4);
        
        // Verify remaining UTXOs
        assert_eq!(utxo_set.len(), 6); // 10 - 4 = 6
        
        // Coinbase UTXO should still exist despite being below pruning height
        let mut coinbase_txid = [0; 32];
        coinbase_txid[0] = 0;
        assert!(utxo_set.exists(&coinbase_txid, 0));
    }
}