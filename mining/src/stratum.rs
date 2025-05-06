//! Stratum Protocol Implementation for SmellyCoin
//!
//! This module implements the Stratum mining protocol (v1) for SmellyCoin,
//! allowing miners to connect to a SmellyCoin node and participate in mining.
//! It follows the standard Stratum protocol with extensions for KAWPOW.

use futures::SinkExt;
use log::{debug, error, info, trace, warn};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};
use thiserror::Error;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::time;
use tokio_util::codec::{Decoder, Encoder, Framed, LinesCodec};

use crate::{MiningJob, MiningJobManager, WorkSubmission};

/// Stratum protocol errors
#[derive(Debug, Error)]
pub enum StratumError {
    /// JSON serialization/deserialization error
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    
    /// I/O error
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    
    /// Protocol error
    #[error("Protocol error: {0}")]
    Protocol(String),
    
    /// Authentication error
    #[error("Authentication error: {0}")]
    Authentication(String),
    
    /// Invalid request
    #[error("Invalid request: {0}")]
    InvalidRequest(String),
}

/// Stratum method types
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StratumMethod {
    /// Mining.subscribe
    Subscribe,
    /// Mining.authorize
    Authorize,
    /// Mining.submit
    Submit,
    /// Mining.get_transactions
    GetTransactions,
    /// Client.get_version
    GetVersion,
    /// Client.show_message
    ShowMessage,
    /// Mining.set_difficulty
    SetDifficulty,
    /// Mining.notify
    Notify,
    /// Unknown method
    Unknown(String),
}

impl From<&str> for StratumMethod {
    fn from(s: &str) -> Self {
        match s {
            "mining.subscribe" => StratumMethod::Subscribe,
            "mining.authorize" => StratumMethod::Authorize,
            "mining.submit" => StratumMethod::Submit,
            "mining.get_transactions" => StratumMethod::GetTransactions,
            "client.get_version" => StratumMethod::GetVersion,
            "client.show_message" => StratumMethod::ShowMessage,
            "mining.set_difficulty" => StratumMethod::SetDifficulty,
            "mining.notify" => StratumMethod::Notify,
            _ => StratumMethod::Unknown(s.to_string()),
        }
    }
}

impl ToString for StratumMethod {
    fn to_string(&self) -> String {
        match self {
            StratumMethod::Subscribe => "mining.subscribe".to_string(),
            StratumMethod::Authorize => "mining.authorize".to_string(),
            StratumMethod::Submit => "mining.submit".to_string(),
            StratumMethod::GetTransactions => "mining.get_transactions".to_string(),
            StratumMethod::GetVersion => "client.get_version".to_string(),
            StratumMethod::ShowMessage => "client.show_message".to_string(),
            StratumMethod::SetDifficulty => "mining.set_difficulty".to_string(),
            StratumMethod::Notify => "mining.notify".to_string(),
            StratumMethod::Unknown(s) => s.clone(),
        }
    }
}

/// Stratum request
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratumRequest {
    /// JSON-RPC ID
    pub id: Option<Value>,
    /// Method name
    pub method: String,
    /// Method parameters
    pub params: Vec<Value>,
}

/// Stratum response
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratumResponse {
    /// JSON-RPC ID
    pub id: Value,
    /// Result (null if error)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    /// Error (null if success)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
}

/// Stratum notification
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StratumNotification {
    /// JSON-RPC ID (null for notifications)
    pub id: Option<Value>,
    /// Method name
    pub method: String,
    /// Method parameters
    pub params: Vec<Value>,
}

/// Stratum session state
#[derive(Debug, Clone)]
pub struct SessionState {
    /// Worker name
    pub worker_name: String,
    /// Worker password (if any)
    pub worker_password: Option<String>,
    /// Subscription ID
    pub subscription_id: String,
    /// Extra nonce 1
    pub extra_nonce1: String,
    /// Extra nonce 2 size
    pub extra_nonce2_size: usize,
    /// Current difficulty
    pub difficulty: f64,
    /// Authorized flag
    pub authorized: bool,
    /// Connection time
    pub connected_at: Instant,
    /// Last activity time
    pub last_activity: Instant,
    /// Shares accepted
    pub shares_accepted: u64,
    /// Shares rejected
    pub shares_rejected: u64,
}

/// Stratum client session
#[derive(Debug)]
pub struct StratumSession {
    /// Session state
    state: Arc<Mutex<SessionState>>,
    /// Client address
    addr: SocketAddr,
    /// Job manager
    job_manager: Arc<MiningJobManager>,
    /// Message sender
    tx: mpsc::Sender<String>,
}

impl StratumSession {
    /// Create a new Stratum session
    pub fn new(
        addr: SocketAddr,
        job_manager: Arc<MiningJobManager>,
        tx: mpsc::Sender<String>,
    ) -> Self {
        // Generate a unique subscription ID
        let subscription_id = format!("{:016x}", rand::random::<u64>());
        
        // Generate a unique extra nonce 1
        let extra_nonce1 = format!("{:08x}", rand::random::<u32>());
        
        let state = SessionState {
            worker_name: String::new(),
            worker_password: None,
            subscription_id,
            extra_nonce1,
            extra_nonce2_size: 4, // 4 bytes for extra nonce 2
            difficulty: 1.0,
            authorized: false,
            connected_at: Instant::now(),
            last_activity: Instant::now(),
            shares_accepted: 0,
            shares_rejected: 0,
        };
        
        StratumSession {
            state: Arc::new(Mutex::new(state)),
            addr,
            job_manager,
            tx,
        }
    }
    
    /// Handle a stratum request
    pub async fn handle_request(&self, request: StratumRequest) -> Result<(), StratumError> {
        // Update last activity time
        {
            let mut state = self.state.lock().unwrap();
            state.last_activity = Instant::now();
        }
        
        // Parse method
        let method = StratumMethod::from(request.method.as_str());
        
        match method {
            StratumMethod::Subscribe => self.handle_subscribe(request).await,
            StratumMethod::Authorize => self.handle_authorize(request).await,
            StratumMethod::Submit => self.handle_submit(request).await,
            StratumMethod::GetTransactions => self.handle_get_transactions(request).await,
            _ => {
                // Unknown or unsupported method
                let response = StratumResponse {
                    id: request.id.unwrap_or(Value::Null),
                    result: None,
                    error: Some(json!([20, "Unsupported method", null])),
                };
                
                self.send_response(response).await
            }
        }
    }
    
    /// Handle mining.subscribe
    async fn handle_subscribe(&self, request: StratumRequest) -> Result<(), StratumError> {
        let state = self.state.lock().unwrap();
        
        // Format: [[mining.set_difficulty, subscription_id], [mining.notify, subscription_id], extra_nonce1, extra_nonce2_size]
        let result = json!([
            [
                ["mining.set_difficulty", state.subscription_id.clone()],
                ["mining.notify", state.subscription_id.clone()]
            ],
            state.extra_nonce1.clone(),
            state.extra_nonce2_size
        ]);
        
        let response = StratumResponse {
            id: request.id.unwrap_or(Value::Null),
            result: Some(result),
            error: None,
        };
        
        drop(state);
        self.send_response(response).await
    }
    
    /// Handle mining.authorize
    async fn handle_authorize(&self, request: StratumRequest) -> Result<(), StratumError> {
        // Extract worker name and password
        let worker_name = match request.params.get(0) {
            Some(Value::String(name)) => name.clone(),
            _ => return Err(StratumError::Protocol("Invalid worker name".into())),
        };
        
        let worker_password = match request.params.get(1) {
            Some(Value::String(pass)) => Some(pass.clone()),
            _ => None,
        };
        
        // Update session state
        {
            let mut state = self.state.lock().unwrap();
            state.worker_name = worker_name.clone();
            state.worker_password = worker_password;
            state.authorized = true; // In a real implementation, this would check credentials
        }
        
        // Send success response
        let response = StratumResponse {
            id: request.id.unwrap_or(Value::Null),
            result: Some(Value::Bool(true)),
            error: None,
        };
        
        self.send_response(response).await?;
        
        // Send initial difficulty
        self.send_set_difficulty(1.0).await?;
        
        // Send initial job
        self.send_job().await
    }
    
    /// Handle mining.submit
    async fn handle_submit(&self, request: StratumRequest) -> Result<(), StratumError> {
        // Check if authorized
        let state = self.state.lock().unwrap();
        if !state.authorized {
            return Err(StratumError::Authentication("Not authorized".into()));
        }
        
        // Extract parameters
        let worker_name = match request.params.get(0) {
            Some(Value::String(name)) => name.clone(),
            _ => return Err(StratumError::Protocol("Invalid worker name".into())),
        };
        
        let job_id = match request.params.get(1) {
            Some(Value::String(id)) => id.clone(),
            _ => return Err(StratumError::Protocol("Invalid job ID".into())),
        };
        
        let extra_nonce2 = match request.params.get(2) {
            Some(Value::String(nonce2)) => hex::decode(nonce2)?,
            _ => return Err(StratumError::Protocol("Invalid extra nonce 2".into())),
        };
        
        let time = match request.params.get(3) {
            Some(Value::String(time_str)) => u32::from_str_radix(time_str, 16)?,
            _ => return Err(StratumError::Protocol("Invalid time".into())),
        };
        
        let nonce = match request.params.get(4) {
            Some(Value::String(nonce_str)) => u64::from_str_radix(nonce_str, 16)?,
            _ => return Err(StratumError::Protocol("Invalid nonce".into())),
        };
        
        drop(state);
        
        // Create work submission
        let submission = WorkSubmission {
            worker_name: worker_name.clone(),
            job_id: job_id.clone(),
            nonce,
            extra_nonce2,
            time,
        };
        
        // Process submission
        let result = match self.job_manager.process_submission(submission) {
            Ok(valid) => {
                // Update share statistics
                let mut state = self.state.lock().unwrap();
                if valid {
                    state.shares_accepted += 1;
                } else {
                    state.shares_rejected += 1;
                }
                valid
            },
            Err(e) => {
                error!("Error processing submission: {}", e);
                false
            },
        };
        
        // Send response
        let response = StratumResponse {
            id: request.id.unwrap_or(Value::Null),
            result: Some(Value::Bool(result)),
            error: if result { None } else { Some(json!([21, "Share rejected", null])) },
        };
        
        self.send_response(response).await
    }
    
    /// Handle mining.get_transactions
    async fn handle_get_transactions(&self, request: StratumRequest) -> Result<(), StratumError> {
        // In a real implementation, this would return transaction hashes
        let response = StratumResponse {
            id: request.id.unwrap_or(Value::Null),
            result: Some(json!([])),
            error: None,
        };
        
        self.send_response(response).await
    }
    
    /// Send a set_difficulty notification
    async fn send_set_difficulty(&self, difficulty: f64) -> Result<(), StratumError> {
        // Update session state
        {
            let mut state = self.state.lock().unwrap();
            state.difficulty = difficulty;
        }
        
        // Send notification
        let notification = json!({
            "id": null,
            "method": "mining.set_difficulty",
            "params": [difficulty]
        });
        
        self.send_json(notification).await
    }
    
    /// Send a job notification
    async fn send_job(&self) -> Result<(), StratumError> {
        // Get current job
        let jobs = self.job_manager.jobs.read().unwrap();
        let job = match jobs.values().max_by_key(|job| job.time) {
            Some(job) => job.clone(),
            None => return Ok(()), // No jobs available
        };
        
        // Get session state
        let state = self.state.lock().unwrap();
        
        // Format job params
        // [job_id, prev_hash, coinbase1, coinbase2, merkle_branches, version, bits, time, clean_job]
        let params = json!([
            job.id,
            hex::encode(job.prev_hash),
            hex::encode(&job.coinbase_tx[0..42]), // Coinbase part 1 (before extranonce)
            hex::encode(&job.coinbase_tx[42..]),  // Coinbase part 2 (after extranonce)
            job.merkle_branches.iter().map(|h| hex::encode(h)).collect::<Vec<_>>(),
            format!("{:08x}", job.version),
            format!("{:08x}", job.bits),
            format!("{:08x}", job.time),
            job.clean_job
        ]);
        
        // Send notification
        let notification = json!({
            "id": null,
            "method": "mining.notify",
            "params": params
        });
        
        drop(state);
        self.send_json(notification).await
    }
    
    /// Send a response
    async fn send_response(&self, response: StratumResponse) -> Result<(), StratumError> {
        let json = serde_json::to_string(&response)?;
        self.send_raw(json).await
    }
    
    /// Send JSON data
    async fn send_json(&self, json: Value) -> Result<(), StratumError> {
        let json_str = json.to_string();
        self.send_raw(json_str).await
    }
    
    /// Send raw data
    async fn send_raw(&self, data: String) -> Result<(), StratumError> {
        self.tx.send(data).await.map_err(|e| {
            StratumError::Io(std::io::Error::new(std::io::ErrorKind::BrokenPipe, e.to_string()))
        })
    }
            shares_accepted: 0,
            shares_rejected: 0,
        };
        
        StratumSession {
            state: Arc::new(Mutex::new(state)),
            addr,
            job_manager,
            tx,
        }
    }
    
    /// Handle a Stratum request
    pub async fn handle_request(&self, request: StratumRequest) -> Result<(), StratumError> {
        let method = StratumMethod::from(request.method.as_str());
        let id = request.id.unwrap_or(Value::Null);
        
        // Update last activity time
        {
            let mut state = self.state.lock().unwrap();
            state.last_activity = Instant::now();
        }
        
        match method {
            StratumMethod::Subscribe => self.handle_subscribe(id, request.params).await,
            StratumMethod::Authorize => self.handle_authorize(id, request.params).await,
            StratumMethod::Submit => self.handle_submit(id, request.params).await,
            StratumMethod::GetTransactions => self.handle_get_transactions(id).await,
            StratumMethod::GetVersion => self.handle_get_version(id).await,
            _ => {
                // Unknown or unsupported method
                self.send_error(id, 20, &format!("Unsupported method: {}", request.method)).await
            }
        }
    }
    
    /// Handle mining.subscribe
    async fn handle_subscribe(&self, id: Value, params: Vec<Value>) -> Result<(), StratumError> {
        let state = self.state.lock().unwrap();
        
        // Prepare subscription response
        // Format: [[mining.set_difficulty, subscription_id], [mining.notify, subscription_id], extra_nonce1, extra_nonce2_size]
        let result = json!([
            [
                "mining.set_difficulty",
                state.subscription_id
            ],
            [
                "mining.notify",
                state.subscription_id
            ],
            state.extra_nonce1,
            state.extra_nonce2_size
        ]);
        
        self.send_response(id, result).await
    }
    
    /// Handle mining.authorize
    async fn handle_authorize(&self, id: Value, params: Vec<Value>) -> Result<(), StratumError> {
        if params.len() < 1 {
            return self.send_error(id, 21, "Invalid parameters for authorize").await;
        }
        
        let worker_name = params[0].as_str().unwrap_or("").to_string();
        let worker_password = params.get(1).and_then(|p| p.as_str()).map(|s| s.to_string());
        
        // In a real implementation, we would validate credentials here
        // For simplicity, we'll accept any credentials
        
        // Update session state
        {
            let mut state = self.state.lock().unwrap();
            state.worker_name = worker_name.clone();
            state.worker_password = worker_password;
            state.authorized = true;
        }
        
        info!("Worker authorized: {}", worker_name);
        
        // Send difficulty first
        self.send_set_difficulty(1.0).await?;
        
        // Send initial job notification
        // TODO: Send actual job
        
        // Send authorization success
        self.send_response(id, json!(true)).await
    }
    
    /// Handle mining.submit
    async fn handle_submit(&self, id: Value, params: Vec<Value>) -> Result<(), StratumError> {
        if params.len() < 5 {
            return self.send_error(id, 21, "Invalid parameters for submit").await;
        }
        
        let worker_name = params[0].as_str().unwrap_or("").to_string();
        let job_id = params[1].as_str().unwrap_or("").to_string();
        let extra_nonce2 = params[2].as_str().unwrap_or("");
        let time = params[3].as_str().unwrap_or("");
        let nonce = params[4].as_str().unwrap_or("");
        
        // Parse parameters
        let extra_nonce2_bytes = hex::decode(extra_nonce2)
            .map_err(|_| StratumError::Protocol("Invalid extra_nonce2 format".to_string()))?;
            
        let time_value = u32::from_str_radix(time, 16)
            .map_err(|_| StratumError::Protocol("Invalid time format".to_string()))?;
            
        let nonce_value = u64::from_str_radix(nonce, 16)
            .map_err(|_| StratumError::Protocol("Invalid nonce format".to_string()))?;
        
        // Create work submission
        let submission = WorkSubmission {
            worker_name: worker_name.clone(),
            job_id: job_id.clone(),
            nonce: nonce_value,
            extra_nonce2: extra_nonce2_bytes,
            time: time_value,
        };
        
        // Process the submission
        match self.job_manager.process_submission(submission) {
            Ok(valid) => {
                // Update share statistics
                {
                    let mut state = self.state.lock().unwrap();
                    if valid {
                        state.shares_accepted += 1;
                        info!("Share accepted from {}: job={}, nonce={}", worker_name, job_id, nonce);
                    } else {
                        state.shares_rejected += 1;
                        warn!("Share rejected from {}: job={}, nonce={}", worker_name, job_id, nonce);
                    }
                }
                
                // Send response
                self.send_response(id, json!(valid)).await
            },
            Err(e) => {
                // Update share statistics
                {
                    let mut state = self.state.lock().unwrap();
                    state.shares_rejected += 1;
                }
                
                warn!("Share error from {}: {}", worker_name, e);
                self.send_error(id, 20, &format!("Share validation error: {}", e)).await
            }
        }
    }
    
    /// Handle mining.get_transactions
    async fn handle_get_transactions(&self, id: Value) -> Result<(), StratumError> {
        // This method is optional in Stratum protocol
        // It returns the list of transactions in the current block template
        // For simplicity, we'll return an empty array
        self.send_response(id, json!([])).await
    }
    
    /// Handle client.get_version
    async fn handle_get_version(&self, id: Value) -> Result<(), StratumError> {
        // Return version information
        self.send_response(id, json!("SmellyCoin Stratum Server v0.1.0")).await
    }
    
    /// Send a job notification
    pub async fn send_job_notification(&self, job: &MiningJob) -> Result<(), StratumError> {
        // Format job notification parameters
        // [job_id, prev_hash, coinbase1, coinbase2, merkle_branches, version, bits, time, clean_job]
        let params = json!([
            job.id,
            hex::encode(job.prev_hash),
            hex::encode(&job.coinbase_tx[..job.coinbase_tx.len() / 2]), // Coinbase part 1
            hex::encode(&job.coinbase_tx[job.coinbase_tx.len() / 2..]), // Coinbase part 2
            job.merkle_branches.iter().map(|h| hex::encode(h)).collect::<Vec<_>>(),
            format!("{:08x}", job.version),
            format!("{:08x}", job.bits),
            format!("{:08x}", job.time),
            job.clean_job
        ]);
        
        // Create notification
        let notification = json!({
            "id": null,
            "method": "mining.notify",
            "params": params
        });
        
        // Send notification
        self.tx.send(notification.to_string()).await
            .map_err(|e| StratumError::Io(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())))
    }
    
    /// Send difficulty notification
    pub async fn send_set_difficulty(&self, difficulty: f64) -> Result<(), StratumError> {
        // Update session state
        {
            let mut state = self.state.lock().unwrap();
            state.difficulty = difficulty;
        }
        
        // Create notification
        let notification = json!({
            "id": null,
            "method": "mining.set_difficulty",
            "params": [difficulty]
        });
        
        // Send notification
        self.tx.send(notification.to_string()).await
            .map_err(|e| StratumError::Io(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())))
    }
    
    /// Send a response
    async fn send_response(&self, id: Value, result: Value) -> Result<(), StratumError> {
        let response = json!({
            "id": id,
            "result": result,
            "error": null
        });
        
        self.tx.send(response.to_string()).await
            .map_err(|e| StratumError::Io(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())))
    }
    
    /// Send an error
    async fn send_error(&self, id: Value, code: i32, message: &str) -> Result<(), StratumError> {
        let response = json!({
            "id": id,
            "result": null,
            "error": [code, message, null]
        });
        
        self.tx.send(response.to_string()).await
            .map_err(|e| StratumError::Io(std::io::Error::new(std::io::ErrorKind::Other, e.to_string())))
    }
}

/// Stratum server
#[derive(Debug)]
pub struct StratumServer {
    /// Server bind address
    bind_addr: SocketAddr,
    /// Job manager
    job_manager: Arc<MiningJobManager>,
    /// Active sessions
    sessions: Arc<RwLock<HashMap<SocketAddr, Arc<StratumSession>>>>,
    /// Server running flag
    running: Arc<Mutex<bool>>,
}

impl StratumServer {
    /// Create a new Stratum server
    pub fn new(bind_addr: SocketAddr, job_manager: Arc<MiningJobManager>) -> Self {
        StratumServer {
            bind_addr,
            job_manager,
            sessions: Arc::new(RwLock::new(HashMap::new())),
            running: Arc::new(Mutex::new(false)),
        }
    }
    
    /// Start the Stratum server
    pub async fn start(&self) -> Result<(), StratumError> {
        // Set running flag
        {
            let mut running = self.running.lock().unwrap();
            *running = true;
        }
        
        // Create TCP listener
        let listener = TcpListener::bind(&self.bind_addr).await?;
        info!("Stratum server listening on {}", self.bind_addr);
        
        // Start session cleanup task
        let sessions = self.sessions.clone();
        let running = self.running.clone();
        tokio::spawn(async move {
            let mut interval = time::interval(Duration::from_secs(60));
            while *running.lock().unwrap() {
                interval.tick().await;
                Self::cleanup_sessions(sessions.clone()).await;
            }
        });
        
        // Accept connections
        while *self.running.lock().unwrap() {
            match listener.accept().await {
                Ok((socket, addr)) => {
                    info!("New Stratum connection from {}", addr);
                    self.handle_connection(socket, addr).await;
                }
                Err(e) => {
                    error!("Error accepting connection: {}", e);
                }
            }
        }
        
        Ok(())
    }
    
    /// Stop the Stratum server
    pub async fn stop(&self) -> Result<(), StratumError> {
        // Set running flag
        {
            let mut running = self.running.lock().unwrap();
            *running = false;
        }
        
        // Close all sessions
        let sessions = self.sessions.read().unwrap();
        for (addr, _) in sessions.iter() {
            info!("Closing Stratum connection to {}", addr);
        }
        
        Ok(())
    }
    
    /// Handle a new connection
    async fn handle_connection(&self, socket: TcpStream, addr: SocketAddr) {
        // Create message channels
        let (tx, mut rx) = mpsc::channel::<String>(100);
        
        // Create session
        let session = Arc::new(StratumSession::new(
            addr,
            self.job_manager.clone(),
            tx,
        ));
        
        // Store session
        {
            let mut sessions = self.sessions.write().unwrap();
            sessions.insert(addr, session.clone());
        }
        
        // Split socket
        let (reader, writer) = socket.into_split();
        
        // Create framed transport
        let mut lines_codec = LinesCodec::new();
        lines_codec.set_max_length(65536); // 64KB max line length
        let mut reader = tokio_util::codec::FramedRead::new(reader, lines_codec);
        let mut writer = tokio::io::BufWriter::new(writer);
        
        // Spawn reader task
        let session_clone = session.clone();
        let sessions_clone = self.sessions.clone();
        tokio::spawn(async move {
            while let Some(line) = reader.next().await {
                match line {
                    Ok(line) => {
                        trace!("Received from {}: {}", addr, line);
                        
                        // Parse request
                        match serde_json::from_str::<StratumRequest>(&line) {
                            Ok(request) => {
                                if let Err(e) = session_clone.handle_request(request).await {
                                    error!("Error handling request from {}: {}", addr, e);
                                }
                            }
                            Err(e) => {
                                error!("Invalid Stratum request from {}: {}", addr, e);
                            }
                        }
                    }
                    Err(e) => {
                        error!("Error reading from {}: {}", addr, e);
                        break;
                    }
                }
            }
            
            // Connection closed
            info!("Stratum connection closed: {}", addr);
            
            // Remove session
            let mut sessions = sessions_clone.write().unwrap();
            sessions.remove(&addr);
        });
        
        // Spawn writer task
        tokio::spawn(async move {
            while let Some(message) = rx.recv().await {
                trace!("Sending to {}: {}", addr, message);
                
                // Write message with newline
                if let Err(e) = writer.write_all(format!("{}{}", message, "\n").as_bytes()).await {
                    error!("Error writing to {}: {}", addr, e);
                    break;
                }
                
                // Flush writer
                if let Err(e) = writer.flush().await {
                    error!("Error flushing to {}: {}", addr, e);
                    break;
                }
            }
        });
    }
    
    /// Broadcast a job to all connected sessions
    pub async fn broadcast_job(&self, job: &MiningJob) -> Result<(), StratumError> {
        let sessions = self.sessions.read().unwrap();
        for (_, session) in sessions.iter() {
            if let Err(e) = session.send_job_notification(job).await {
                warn!("Error sending job notification: {}", e);
            }
        }
        
        Ok(())
    }
    
    /// Clean up inactive sessions
    async fn cleanup_sessions(sessions: Arc<RwLock<HashMap<SocketAddr, Arc<StratumSession>>>>) {
        let now = Instant::now();
        let mut to_remove = Vec::new();
        
        // Find inactive sessions
        {
            let sessions_guard = sessions.read().unwrap();
            for (addr, session) in sessions_guard.iter() {
                let state = session.state.lock().unwrap();
                let idle_time = now.duration_since(state.last_activity);
                
                // If idle for more than 10 minutes, mark for removal
                if idle_time > Duration::from_secs(600) {
                    to_remove.push(*addr);
                }
            }
        }
        
        // Remove inactive sessions
        if !to_remove.is_empty() {
            let mut sessions_guard = sessions.write().unwrap();
            for addr in to_remove {
                info!("Removing inactive Stratum session: {}", addr);
                sessions_guard.remove(&addr);
            }
        }
    }
    
    /// Get the number of connected clients
    pub fn connected_clients(&self) -> usize {
        let sessions = self.sessions.read().unwrap();
        sessions.len()
    }
    
    /// Get mining statistics
    pub fn get_stats(&self) -> (usize, u64, u64) {
        let sessions = self.sessions.read().unwrap();
        let mut shares_accepted = 0;
        let mut shares_rejected = 0;
        
        for (_, session) in sessions.iter() {
            let state = session.state.lock().unwrap();
            shares_accepted += state.shares_accepted;
            shares_rejected += state.shares_rejected;
        }
        
        (sessions.len(), shares_accepted, shares_rejected)
    }
}