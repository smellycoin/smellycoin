//! SmellyCoin Node - Main Entry Point
//!
//! This file serves as the entry point for the SmellyCoin cryptocurrency node.
//! It initializes the application, parses command-line arguments, and starts
//! the various services required for node operation.

use clap::{Parser, Subcommand};
use log::{info, warn, error};
use std::path::PathBuf;
use std::process;
use std::sync::Arc;
use std::str::FromStr;

use smellycoin_core::Address;

/// Command line arguments for the SmellyCoin node
#[derive(Parser)]
#[clap(name = "smellycoin")]
#[clap(about = "SmellyCoin - A high-performance PoW cryptocurrency with KAWPOW algorithm")]
struct Cli {
    /// Sets a custom config file
    #[clap(short, long, value_name = "FILE")]
    config: Option<PathBuf>,

    /// Network to connect to (mainnet, testnet, regtest)
    #[clap(long, default_value = "mainnet")]
    network: String,

    /// Enable mining
    #[clap(long)]
    mine: bool,

    /// Mining address to receive block rewards
    #[clap(long)]
    mining_address: Option<String>,

    /// Number of mining threads (CPU mining only)
    #[clap(long, default_value = "1")]
    threads: usize,

    /// Enable JSON-RPC server
    #[clap(long, default_value = "true")]
    rpc: bool,

    /// JSON-RPC bind address
    #[clap(long, default_value = "127.0.0.1:8332")]
    rpc_bind: String,

    /// Enable Stratum mining server
    #[clap(long)]
    stratum: bool,

    /// Stratum server bind address
    #[clap(long, default_value = "127.0.0.1:3333")]
    stratum_bind: String,

    /// Data directory
    #[clap(long, value_name = "DIR")]
    datadir: Option<PathBuf>,

    /// Verbosity level (0-5)
    #[clap(short, long, default_value = "3")]
    verbosity: u8,

    /// Subcommands
    #[clap(subcommand)]
    command: Option<Commands>,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize a new blockchain
    Init {
        /// Force initialization even if data directory exists
        #[clap(long)]
        force: bool,
    },
    /// Import blocks from file
    Import {
        /// Path to blocks file
        #[clap(value_name = "FILE")]
        file: PathBuf,
    },
    /// Export blocks to file
    Export {
        /// Path to output file
        #[clap(value_name = "FILE")]
        file: PathBuf,
        /// Start block height
        #[clap(long, default_value = "0")]
        start: u64,
        /// End block height (inclusive)
        #[clap(long)]
        end: Option<u64>,
    },
}

/// Initialize logging based on verbosity level
fn init_logging(verbosity: u8) {
    let log_level = match verbosity {
        0 => log::LevelFilter::Error,
        1 => log::LevelFilter::Warn,
        2 => log::LevelFilter::Info,
        3 => log::LevelFilter::Debug,
        _ => log::LevelFilter::Trace,
    };

    env_logger::Builder::new()
        .filter_level(log_level)
        .format_timestamp_millis()
        .init();
}

/// Parse a SmellyCoin address string into an Address type
fn parse_address(address: &str) -> Result<Address, String> {
    // Check address prefix
    if !address.starts_with("smc") {
        return Err("Address must start with 'smc'".to_string());
    }
    
    // Remove prefix
    let addr_without_prefix = &address[3..];
    
    // Decode from base58
    let decoded = match bs58::decode(addr_without_prefix).into_vec() {
        Ok(bytes) => bytes,
        Err(_) => return Err("Invalid base58 encoding".to_string()),
    };
    
    // Check length (20 bytes address + 4 bytes checksum)
    if decoded.len() != 24 {
        return Err(format!("Invalid address length: {}", decoded.len()));
    }
    
    // Extract address and checksum
    let addr_bytes = &decoded[0..20];
    let checksum = &decoded[20..24];
    
    // Verify checksum (simplified for now)
    // In a real implementation, we would calculate a proper checksum
    
    // Convert to Address type
    let mut address_bytes = [0u8; 20];
    address_bytes.copy_from_slice(addr_bytes);
    
    Ok(address_bytes)
}

/// Application entry point
fn main() {
    // Parse command line arguments
    let cli = Cli::parse();
    
    // Initialize logging
    init_logging(cli.verbosity);
    
    info!("Starting SmellyCoin node");
    
    // TODO: Load configuration from file if specified
    
    // Handle subcommands
    if let Some(cmd) = cli.command {
        match cmd {
            Commands::Init { force } => {
                info!("Initializing blockchain (force: {})", force);
                // TODO: Initialize blockchain
            }
            Commands::Import { file } => {
                info!("Importing blocks from {}", file.display());
                // TODO: Import blocks
            }
            Commands::Export { file, start, end } => {
                info!(
                    "Exporting blocks from {} to {} to {}", 
                    start, 
                    end.map_or("end".to_string(), |e| e.to_string()), 
                    file.display()
                );
                // TODO: Export blocks
            }
        }
        process::exit(0);
    }
    
    // Start node services
    info!("Network: {}", cli.network);
    
    if cli.rpc {
        info!("Starting JSON-RPC server on {}", cli.rpc_bind);
        // TODO: Start RPC server
    }
    
    if cli.stratum {
        info!("Starting Stratum mining server on {}", cli.stratum_bind);
        
        if let Some(addr_str) = &cli.mining_address {
            // Parse mining address
            let mining_address = match parse_address(addr_str) {
                Ok(addr) => addr,
                Err(e) => {
                    error!("Invalid mining address: {}", e);
                    process::exit(1);
                }
            };
            
            // Create mining job manager if not already created
            let kawpow_params = smellycoin_consensus::KawpowParams::default();
            let job_manager = Arc::new(smellycoin_mining::MiningJobManager::new(
                mining_address,
                kawpow_params.clone(),
                Box::new(|block| {
                    info!("New block found: height={}, hash={}", 
                          block.height.unwrap_or(0),
                          hex::encode(&block.hash()[0..8]));
                    // TODO: Submit block to blockchain
                }),
            ));
            
            // Parse bind address
            let bind_addr = match cli.stratum_bind.parse() {
                Ok(addr) => addr,
                Err(e) => {
                    error!("Invalid stratum bind address: {}", e);
                    process::exit(1);
                }
            };
            
            // Create and start Stratum server
            let stratum_server = smellycoin_mining::stratum::StratumServer::new(
                bind_addr,
                job_manager.clone(),
            );
            
            tokio::spawn(async move {
                if let Err(e) = stratum_server.start().await {
                    error!("Failed to start Stratum server: {}", e);
                }
            });
        } else {
            error!("Stratum server requires a mining address to be specified");
            process::exit(1);
        }
    }
    
    if cli.mine {
        if let Some(addr_str) = &cli.mining_address {
            info!("Starting miner with {} threads, mining to {}", cli.threads, addr_str);
            
            // Parse mining address
            let mining_address = match parse_address(addr_str) {
                Ok(addr) => addr,
                Err(e) => {
                    error!("Invalid mining address: {}", e);
                    process::exit(1);
                }
            };
            
            // Create mining job manager
            let kawpow_params = smellycoin_consensus::KawpowParams::default();
            let job_manager = Arc::new(smellycoin_mining::MiningJobManager::new(
                mining_address,
                kawpow_params.clone(),
                Box::new(|block| {
                    info!("New block found: height={}, hash={}", 
                          block.height.unwrap_or(0),
                          hex::encode(&block.hash()[0..8]));
                    // TODO: Submit block to blockchain
                }),
            ));
            
            // Start CPU miner if requested
            let cpu_miner = smellycoin_mining::cpu::CpuMiner::new(
                mining_address,
                cli.threads,
                kawpow_params,
                job_manager.clone(),
            );
            
            tokio::spawn(async move {
                if let Err(e) = cpu_miner.start().await {
                    error!("Failed to start CPU miner: {}", e);
                }
            });
        } else {
            warn!("Mining enabled but no mining address specified");
        }
    }
    
    // Start node main loop
    
    info!("SmellyCoin node started");
    
    // Keep the main thread running
    tokio::runtime::Runtime::new()
        .unwrap()
        .block_on(async {
            loop {
                tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
            }
        });
}
}