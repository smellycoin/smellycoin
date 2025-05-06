//! Peer Management Module
//!
//! This module handles peer discovery, connection management, and message exchange
//! between nodes in the SmellyCoin network.

use std::collections::{HashMap, HashSet};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};

use log::{debug, error, info, trace, warn};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, RwLock};
use tokio::time;

use smellycoin_core::{Block, Hash, Network, Transaction};

use crate::message::{Message, MessageType};
use crate::NetworkConfig;

/// Peer error types
#[derive(Debug, Error)]
pub enum PeerError {
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    
    /// Protocol error
    #[error("Protocol error: {0}")]
    Protocol(String),
    
    /// Connection error
    #[error("Connection error: {0}")]
    Connection(String),
    
    /// Peer banned
    #[error("Peer is banned")]
    Banned,
    
    /// Connection limit reached
    #[error("Connection limit reached")]
    ConnectionLimitReached,
    
    /// Already connected
    #[error("Already connected to peer")]
    AlreadyConnected,
    
    /// Handshake error
    #[error("Handshake error: {0}")]
    Handshake(String),
}

/// Peer information
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerInfo {
    /// Peer address
    pub addr: SocketAddr,
    
    /// User agent
    pub user_agent: String,
    
    /// Protocol version
    pub protocol_version: u32,
    
    /// Services offered by the peer
    pub services: u64,
    
    /// Best block height
    pub block_height: u64,
    
    /// Connection time
    pub connected_since: u64,
    
    /// Bytes sent
    pub bytes_sent: u64,
    
    /// Bytes received
    pub bytes_received: u64,
    
    /// Ping time in milliseconds
    pub ping_time: Option<u64>,
    
    /// Whether this is an outbound connection
    pub outbound: bool,
}

/// Peer connection state
#[derive(Debug, PartialEq, Eq, Clone, Copy)]
enum PeerState {
    /// Initial state
    Initial,
    
    /// Handshake in progress
    Handshaking,
    
    /// Connected and ready
    Connected,
    
    /// Disconnecting
    Disconnecting,
    
    /// Disconnected
    Disconnected,
}

/// Peer connection
pub struct Peer {
    /// Peer address
    addr: SocketAddr,
    
    /// Peer information
    info: RwLock<PeerInfo>,
    
    /// Connection state
    state: RwLock<PeerState>,
    
    /// Message sender
    tx: mpsc::Sender<Message>,
    
    /// Last seen time
    last_seen: RwLock<Instant>,
    
    /// Network configuration
    config: NetworkConfig,
}

impl Peer {
    /// Create a new peer
    pub fn new(addr: SocketAddr, tx: mpsc::Sender<Message>, config: NetworkConfig, outbound: bool) -> Self {
        let info = PeerInfo {
            addr,
            user_agent: String::new(),
            protocol_version: 0,
            services: 0,
            block_height: 0,
            connected_since: chrono::Utc::now().timestamp() as u64,
            bytes_sent: 0,
            bytes_received: 0,
            ping_time: None,
            outbound,
        };
        
        Peer {
            addr,
            info: RwLock::new(info),
            state: RwLock::new(PeerState::Initial),
            tx,
            last_seen: RwLock::new(Instant::now()),
            config,
        }
    }
    
    /// Get peer address
    pub fn addr(&self) -> SocketAddr {
        self.addr
    }
    
    /// Get peer information
    pub async fn info(&self) -> PeerInfo {
        self.info.read().await.clone()
    }
    
    /// Get peer state
    pub async fn state(&self) -> PeerState {
        *self.state.read().await
    }
    
    /// Set peer state
    pub async fn set_state(&self, state: PeerState) {
        *self.state.write().await = state;
    }
    
    /// Update last seen time
    pub async fn update_last_seen(&self) {
        *self.last_seen.write().await = Instant::now();
    }
    
    /// Send a message to the peer
    pub async fn send_message(&self, message: Message) -> Result<(), PeerError> {
        if *self.state.read().await != PeerState::Connected {
            return Err(PeerError::Connection("Peer not connected".to_string()));
        }
        
        self.tx.send(message).await
            .map_err(|_| PeerError::Connection("Failed to send message".to_string()))
    }
    
    /// Send a ping message
    pub async fn send_ping(&self) -> Result<(), PeerError> {
        self.send_message(Message::new(MessageType::Ping, vec![])).await
    }
    
    /// Send a version message
    pub async fn send_version(&self) -> Result<(), PeerError> {
        // In a real implementation, this would serialize a version message
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::Version, payload)).await
    }
    
    /// Send a block message
    pub async fn send_block(&self, block: &Block) -> Result<(), PeerError> {
        // In a real implementation, this would serialize the block
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::Block, payload)).await
    }
    
    /// Send a transaction message
    pub async fn send_transaction(&self, tx: &Transaction) -> Result<(), PeerError> {
        // In a real implementation, this would serialize the transaction
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::Tx, payload)).await
    }
    
    /// Send a get_headers message
    pub async fn send_get_headers(&self, locator_hashes: Vec<Hash>, stop_hash: Hash) -> Result<(), PeerError> {
        // In a real implementation, this would serialize the locator hashes and stop hash
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::GetHeaders, payload)).await
    }
    
    /// Send a get_blocks message
    pub async fn send_get_blocks(&self, locator_hashes: Vec<Hash>, stop_hash: Hash) -> Result<(), PeerError> {
        // In a real implementation, this would serialize the locator hashes and stop hash
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::GetBlocks, payload)).await
    }
    
    /// Send a get_data message
    pub async fn send_get_data(&self, inventory: Vec<(u32, Hash)>) -> Result<(), PeerError> {
        // In a real implementation, this would serialize the inventory
        let payload = vec![]; // Placeholder
        self.send_message(Message::new(MessageType::GetData, payload)).await
    }
}

/// Peer manager
pub struct PeerManager {
    /// Connected peers
    peers: RwLock<HashMap<SocketAddr, Arc<Peer>>>,
    
    /// Banned peers
    banned: RwLock<HashSet<SocketAddr>>,
    
    /// Network configuration
    config: NetworkConfig,
}

impl PeerManager {
    /// Create a new peer manager
    pub fn new(config: NetworkConfig) -> Self {
        PeerManager {
            peers: RwLock::new(HashMap::new()),
            banned: RwLock::new(HashSet::new()),
            config,
        }
    }
    
    /// Connect to a peer
    pub async fn connect(&self, addr: SocketAddr) -> Result<Arc<Peer>, PeerError> {
        // Check if we're already connected to this peer
        if self.peers.read().await.contains_key(&addr) {
            return Err(PeerError::AlreadyConnected);
        }
        
        // Check if the peer is banned
        if self.banned.read().await.contains(&addr) {
            return Err(PeerError::Banned);
        }
        
        // Check connection limits
        let peers = self.peers.read().await;
        let outbound_count = peers.values()
            .filter(|p| p.info.try_read().unwrap().outbound)
            .count();
        
        if outbound_count >= self.config.max_outbound_connections {
            return Err(PeerError::ConnectionLimitReached);
        }
        
        // Connect to the peer
        let stream = match tokio::time::timeout(
            Duration::from_secs(self.config.connection_timeout_sec),
            TcpStream::connect(addr)
        ).await {
            Ok(Ok(stream)) => stream,
            Ok(Err(e)) => return Err(PeerError::Connection(e.to_string())),
            Err(_) => return Err(PeerError::Connection("Connection timeout".to_string())),
        };
        
        // Create message channels
        let (tx, rx) = mpsc::channel(100);
        
        // Create the peer
        let peer = Arc::new(Peer::new(addr, tx, self.config.clone(), true));
        
        // Start the peer handler
        self.handle_peer(peer.clone(), stream, rx).await;
        
        // Add the peer to our list
        self.peers.write().await.insert(addr, peer.clone());
        
        Ok(peer)
    }
    
    /// Handle a peer connection
    async fn handle_peer<T>(
        &self,
        peer: Arc<Peer>,
        stream: T,
        mut rx: mpsc::Receiver<Message>,
    ) where
        T: AsyncRead + AsyncWrite + Unpin + Send + 'static,
    {
        // Split the stream
        let (mut reader, mut writer) = tokio::io::split(stream);
        
        // Set the peer state to handshaking
        peer.set_state(PeerState::Handshaking).await;
        
        // Start the handshake
        if let Err(e) = peer.send_version().await {
            error!("Failed to send version message to {}: {}", peer.addr(), e);
            peer.set_state(PeerState::Disconnected).await;
            return;
        }
        
        // Spawn a task to handle incoming messages
        let peer_clone = peer.clone();
        tokio::spawn(async move {
            // In a real implementation, this would read and process messages from the peer
            
            // For now, just set the peer as connected after a short delay
            time::sleep(Duration::from_millis(100)).await;
            peer_clone.set_state(PeerState::Connected).await;
            
            // Main message loop
            loop {
                // TODO: Read and process messages
                time::sleep(Duration::from_secs(1)).await;
            }
        });
        
        // Spawn a task to handle outgoing messages
        let peer_clone = peer.clone();
        tokio::spawn(async move {
            while let Some(msg) = rx.recv().await {
                // In a real implementation, this would serialize and send the message
                
                // Update bytes sent
                let mut info = peer_clone.info.write().await;
                info.bytes_sent += msg.payload.len() as u64;
            }
            
            // If we get here, the channel was closed
            peer_clone.set_state(PeerState::Disconnected).await;
        });
    }
    
    /// Disconnect from a peer
    pub async fn disconnect(&self, addr: SocketAddr) {
        if let Some(peer) = self.peers.read().await.get(&addr) {
            peer.set_state(PeerState::Disconnecting).await;
            // In a real implementation, we would send a disconnect message
            
            // Remove the peer from our list
            self.peers.write().await.remove(&addr);
        }
    }
    
    /// Ban a peer
    pub async fn ban(&self, addr: SocketAddr, duration: Duration) {
        // Add the peer to the banned list
        self.banned.write().await.insert(addr);
        
        // Disconnect if connected
        self.disconnect(addr).await;
        
        // Schedule unbanning
        let banned = self.banned.clone();
        tokio::spawn(async move {
            time::sleep(duration).await;
            banned.write().await.remove(&addr);
        });
    }
    
    /// Get the number of connected peers
    pub async fn peer_count(&self) -> usize {
        self.peers.read().await.len()
    }
    
    /// Get information about all connected peers
    pub async fn connected_peers(&self) -> Vec<PeerInfo> {
        let peers = self.peers.read().await;
        let mut result = Vec::with_capacity(peers.len());
        
        for peer in peers.values() {
            if peer.state().await == PeerState::Connected {
                result.push(peer.info().await);
            }
        }
        
        result
    }
    
    /// Broadcast a block to all connected peers
    pub async fn broadcast_block(&self, block: &Block) {
        let peers = self.peers.read().await;
        
        for peer in peers.values() {
            if peer.state().await == PeerState::Connected {
                if let Err(e) = peer.send_block(block).await {
                    warn!("Failed to send block to {}: {}", peer.addr(), e);
                }
            }
        }
    }
    
    /// Broadcast a transaction to all connected peers
    pub async fn broadcast_transaction(&self, tx: &Transaction) {
        let peers = self.peers.read().await;
        
        for peer in peers.values() {
            if peer.state().await == PeerState::Connected {
                if let Err(e) = peer.send_transaction(tx).await {
                    warn!("Failed to send transaction to {}: {}", peer.addr(), e);
                }
            }
        }
    }
    
    /// Get a random connected peer
    pub async fn random_peer(&self) -> Option<Arc<Peer>> {
        let peers = self.peers.read().await;
        
        if peers.is_empty() {
            return None;
        }
        
        // Get all connected peers
        let connected: Vec<_> = peers.values()
            .filter(|p| matches!(p.state().await, PeerState::Connected))
            .cloned()
            .collect();
        
        if connected.is_empty() {
            return None;
        }
        
        // Select a random peer
        let idx = rand::random::<usize>() % connected.len();
        Some(connected[idx].clone())
    }
}