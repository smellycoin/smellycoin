//! SmellyCoin WASM Module
//!
//! This module provides WebAssembly bindings for SmellyCoin, enabling web-based mining
//! and wallet functionality. It's designed to work in browsers and mobile devices,
//! with optimizations for performance and battery efficiency.

use wasm_bindgen::prelude::*;
use web_sys::{console, Worker};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};

// When the `wee_alloc` feature is enabled, use `wee_alloc` as the global allocator.
#[cfg(feature = "wee_alloc")]
#[global_allocator]
static ALLOC: wee_alloc::WeeAlloc = wee_alloc::WeeAlloc::INIT;

/// Mining configuration
#[wasm_bindgen]
#[derive(Clone, Serialize, Deserialize)]
pub struct MiningConfig {
    /// Pool address (stratum+tcp://...)
    pub pool: String,
    
    /// Wallet address
    pub address: String,
    
    /// Number of threads to use
    pub threads: u8,
    
    /// Throttle factor (0-1)
    pub throttle: f64,
}

/// SmellyCoin WASM miner
#[wasm_bindgen]
pub struct SmellyCoinMiner {
    /// Mining configuration
    config: MiningConfig,
    
    /// Worker threads
    workers: Arc<Mutex<Vec<Worker>>>,
    
    /// Mining status
    is_mining: Arc<Mutex<bool>>,
    
    /// Current hashrate
    hashrate: Arc<Mutex<f64>>,
}

#[wasm_bindgen]
impl SmellyCoinMiner {
    /// Create a new SmellyCoin miner
    #[wasm_bindgen(constructor)]
    pub fn new(config: JsValue) -> Result<SmellyCoinMiner, JsValue> {
        console_error_panic_hook::set_once();
        
        // Parse configuration
        let config: MiningConfig = serde_wasm_bindgen::from_value(config)?;
        
        // Validate configuration
        if config.threads == 0 || config.threads > 16 {
            return Err(JsValue::from_str("Threads must be between 1 and 16"));
        }
        
        if config.throttle < 0.0 || config.throttle > 1.0 {
            return Err(JsValue::from_str("Throttle must be between 0 and 1"));
        }
        
        Ok(SmellyCoinMiner {
            config,
            workers: Arc::new(Mutex::new(Vec::new())),
            is_mining: Arc::new(Mutex::new(false)),
            hashrate: Arc::new(Mutex::new(0.0)),
        })
    }
    
    /// Start mining
    pub fn start(&self) -> Result<(), JsValue> {
        let mut is_mining = self.is_mining.lock().unwrap();
        
        if *is_mining {
            return Ok(()); // Already mining
        }
        
        *is_mining = true;
        
        // Create worker threads
        let mut workers = self.workers.lock().unwrap();
        workers.clear();
        
        for _ in 0..self.config.threads {
            let worker = Worker::new("./smellycoin_worker.js")?;
            
            // Send configuration to worker
            let config = JsValue::from_serde(&self.config).unwrap();
            worker.post_message(&config)?;
            
            workers.push(worker);
        }
        
        console::log_1(&JsValue::from_str("Mining started"));
        Ok(())
    }
    
    /// Stop mining
    pub fn stop(&self) -> Result<(), JsValue> {
        let mut is_mining = self.is_mining.lock().unwrap();
        
        if !*is_mining {
            return Ok(()); // Not mining
        }
        
        *is_mining = false;
        
        // Terminate worker threads
        let mut workers = self.workers.lock().unwrap();
        
        for worker in workers.iter() {
            worker.post_message(&JsValue::from_str("stop"))?;
        }
        
        workers.clear();
        
        // Reset hashrate
        *self.hashrate.lock().unwrap() = 0.0;
        
        console::log_1(&JsValue::from_str("Mining stopped"));
        Ok(())
    }
    
    /// Get current hashrate
    pub fn get_hashrate(&self) -> f64 {
        *self.hashrate.lock().unwrap()
    }
    
    /// Update hashrate (called from worker)
    #[wasm_bindgen(js_name = updateHashrate)]
    pub fn update_hashrate(&self, worker_hashrate: f64) {
        let mut hashrate = self.hashrate.lock().unwrap();
        *hashrate = worker_hashrate;
    }
    
    /// Check if mining is active
    #[wasm_bindgen(js_name = isMining)]
    pub fn is_mining(&self) -> bool {
        *self.is_mining.lock().unwrap()
    }
}

/// SmellyCoin wallet
#[wasm_bindgen]
pub struct SmellyCoinWallet {
    /// Node URL
    node_url: String,
    
    /// Private key (encrypted)
    private_key: Option<String>,
    
    /// Public address
    address: String,
}

#[wasm_bindgen]
impl SmellyCoinWallet {
    /// Create a new SmellyCoin wallet
    #[wasm_bindgen(constructor)]
    pub fn new() -> SmellyCoinWallet {
        console_error_panic_hook::set_once();
        
        // Generate a new key pair
        let (private_key, address) = generate_key_pair();
        
        SmellyCoinWallet {
            node_url: String::new(),
            private_key: Some(private_key),
            address,
        }
    }
    
    /// Connect to a node
    pub fn connect(&mut self, node_url: String) {
        self.node_url = node_url;
        console::log_1(&JsValue::from_str(&format!("Connected to {}", self.node_url)));
    }
    
    /// Generate a new address
    pub fn generate_address(&mut self) -> String {
        let (private_key, address) = generate_key_pair();
        self.private_key = Some(private_key);
        self.address = address.clone();
        address
    }
    
    /// Get the current address
    pub fn get_address(&self) -> String {
        self.address.clone()
    }
    
    /// Get balance
    pub async fn get_balance(&self) -> Result<f64, JsValue> {
        if self.node_url.is_empty() {
            return Err(JsValue::from_str("Not connected to a node"));
        }
        
        // In a real implementation, this would make an API call to the node
        // For now, we'll return a placeholder value
        Ok(0.0)
    }
    
    /// Send a transaction
    pub async fn send_transaction(&self, recipient: String, amount: f64) -> Result<String, JsValue> {
        if self.node_url.is_empty() {
            return Err(JsValue::from_str("Not connected to a node"));
        }
        
        if self.private_key.is_none() {
            return Err(JsValue::from_str("No private key available"));
        }
        
        // In a real implementation, this would create and sign a transaction
        // For now, we'll return a placeholder transaction ID
        Ok("0000000000000000000000000000000000000000000000000000000000000000".to_string())
    }
    
    /// Get transaction history
    pub async fn get_transaction_history(&self) -> Result<JsValue, JsValue> {
        if self.node_url.is_empty() {
            return Err(JsValue::from_str("Not connected to a node"));
        }
        
        // In a real implementation, this would fetch transaction history from the node
        // For now, we'll return an empty array
        Ok(JsValue::from_serde(&Vec::<String>::new()).unwrap())
    }
}

/// Generate a new key pair (private key and address)
fn generate_key_pair() -> (String, String) {
    // In a real implementation, this would generate a cryptographically secure key pair
    // For now, we'll return placeholder values
    let private_key = "0000000000000000000000000000000000000000000000000000000000000001".to_string();
    let address = "0x0000000000000000000000000000000000000001".to_string();
    (private_key, address)
}