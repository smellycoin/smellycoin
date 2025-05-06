//! SmellyCoin JSON-RPC API
//!
//! This module implements the JSON-RPC API for SmellyCoin, allowing external
//! applications to interact with the node, query blockchain data, and submit
//! transactions and blocks.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::{
    extract::{Extension, Json},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
    Router,
};
use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use thiserror::Error;
use tokio::sync::RwLock;

use smellycoin_core::{Address, Block, Hash, Transaction};
use smellycoin_network::{NetworkService, SyncState};
use smellycoin_storage::BlockStore;

pub mod methods;

/// Re-export RPC methods
pub use methods::*;

/// JSON-RPC error codes
pub mod error_codes {
    /// Parse error
    pub const PARSE_ERROR: i32 = -32700;
    /// Invalid request
    pub const INVALID_REQUEST: i32 = -32600;
    /// Method not found
    pub const METHOD_NOT_FOUND: i32 = -32601;
    /// Invalid params
    pub const INVALID_PARAMS: i32 = -32602;
    /// Internal error
    pub const INTERNAL_ERROR: i32 = -32603;
    /// Invalid address
    pub const INVALID_ADDRESS: i32 = -1;
    /// Transaction validation error
    pub const TX_VALIDATION_ERROR: i32 = -2;
    /// Block validation error
    pub const BLOCK_VALIDATION_ERROR: i32 = -3;
    /// Not found
    pub const NOT_FOUND: i32 = -4;
    /// RPC in warming up
    pub const RPC_IN_WARMUP: i32 = -28;
}

/// RPC error
#[derive(Debug, Error)]
pub enum RpcError {
    /// Parse error
    #[error("Parse error: {0}")]
    ParseError(String),
    
    /// Invalid request
    #[error("Invalid request: {0}")]
    InvalidRequest(String),
    
    /// Method not found
    #[error("Method not found: {0}")]
    MethodNotFound(String),
    
    /// Invalid parameters
    #[error("Invalid parameters: {0}")]
    InvalidParams(String),
    
    /// Internal error
    #[error("Internal error: {0}")]
    InternalError(String),
    
    /// Invalid address
    #[error("Invalid address: {0}")]
    InvalidAddress(String),
    
    /// Transaction validation error
    #[error("Transaction validation error: {0}")]
    TxValidationError(String),
    
    /// Block validation error
    #[error("Block validation error: {0}")]
    BlockValidationError(String),
    
    /// Not found
    #[error("Not found: {0}")]
    NotFound(String),
    
    /// RPC in warming up
    #[error("RPC in warming up")]
    RpcInWarmup,
}

impl RpcError {
    /// Get the error code
    pub fn code(&self) -> i32 {
        match self {
            RpcError::ParseError(_) => error_codes::PARSE_ERROR,
            RpcError::InvalidRequest(_) => error_codes::INVALID_REQUEST,
            RpcError::MethodNotFound(_) => error_codes::METHOD_NOT_FOUND,
            RpcError::InvalidParams(_) => error_codes::INVALID_PARAMS,
            RpcError::InternalError(_) => error_codes::INTERNAL_ERROR,
            RpcError::InvalidAddress(_) => error_codes::INVALID_ADDRESS,
            RpcError::TxValidationError(_) => error_codes::TX_VALIDATION_ERROR,
            RpcError::BlockValidationError(_) => error_codes::BLOCK_VALIDATION_ERROR,
            RpcError::NotFound(_) => error_codes::NOT_FOUND,
            RpcError::RpcInWarmup => error_codes::RPC_IN_WARMUP,
        }
    }
}

/// JSON-RPC request
#[derive(Debug, Clone, Deserialize)]
pub struct JsonRpcRequest {
    /// JSON-RPC version
    pub jsonrpc: String,
    
    /// Method name
    pub method: String,
    
    /// Parameters
    pub params: Option<Value>,
    
    /// Request ID
    pub id: Option<Value>,
}

/// JSON-RPC response
#[derive(Debug, Clone, Serialize)]
pub struct JsonRpcResponse {
    /// JSON-RPC version
    pub jsonrpc: String,
    
    /// Result
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    
    /// Error
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
    
    /// Request ID
    pub id: Value,
}

/// JSON-RPC error
#[derive(Debug, Clone, Serialize)]
pub struct JsonRpcError {
    /// Error code
    pub code: i32,
    
    /// Error message
    pub message: String,
    
    /// Error data
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl From<RpcError> for JsonRpcError {
    fn from(error: RpcError) -> Self {
        JsonRpcError {
            code: error.code(),
            message: error.to_string(),
            data: None,
        }
    }
}

/// RPC context
#[derive(Clone)]
pub struct RpcContext {
    /// Block store
    pub block_store: Arc<dyn BlockStore>,
    
    /// Network service
    pub network: Arc<RwLock<NetworkService>>,
}

/// RPC configuration
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RpcConfig {
    /// Bind address
    pub bind_addr: SocketAddr,
    
    /// Username for authentication
    pub username: Option<String>,
    
    /// Password for authentication
    pub password: Option<String>,
    
    /// Allow CORS from these origins
    pub cors_domains: Vec<String>,
    
    /// Maximum number of concurrent requests
    pub max_concurrent_requests: usize,
}

impl Default for RpcConfig {
    fn default() -> Self {
        RpcConfig {
            bind_addr: "127.0.0.1:8332".parse().unwrap(),
            username: None,
            password: None,
            cors_domains: vec![],
            max_concurrent_requests: 100,
        }
    }
}

/// RPC server
pub struct RpcServer {
    /// RPC configuration
    config: RpcConfig,
    
    /// RPC context
    context: RpcContext,
}

impl RpcServer {
    /// Create a new RPC server
    pub fn new(config: RpcConfig, context: RpcContext) -> Self {
        RpcServer {
            config,
            context,
        }
    }
    
    /// Start the RPC server
    pub async fn start(&self) -> Result<(), Box<dyn std::error::Error>> {
        info!("Starting JSON-RPC server on {}", self.config.bind_addr);
        
        // Create the router
        let app = Router::new()
            .route("/", post(handle_rpc_request))
            .layer(Extension(self.context.clone()));
        
        // Start the server
        axum::Server::bind(&self.config.bind_addr)
            .serve(app.into_make_service())
            .await?;
        
        Ok(())
    }
}

/// Handle a JSON-RPC request
async fn handle_rpc_request(
    Extension(context): Extension<RpcContext>,
    Json(request): Json<JsonRpcRequest>,
) -> impl IntoResponse {
    debug!("Received RPC request: {}", request.method);
    
    // Validate JSON-RPC version
    if request.jsonrpc != "2.0" {
        return create_error_response(
            RpcError::InvalidRequest("Invalid JSON-RPC version".to_string()),
            request.id.unwrap_or(Value::Null),
        );
    }
    
    // Get the request ID
    let id = request.id.unwrap_or(Value::Null);
    
    // Dispatch the method
    let result = match request.method.as_str() {
        // Blockchain methods
        "getbestblockhash" => methods::get_best_block_hash(&context, request.params).await,
        "getblock" => methods::get_block(&context, request.params).await,
        "getblockchaininfo" => methods::get_blockchain_info(&context, request.params).await,
        "getblockcount" => methods::get_block_count(&context, request.params).await,
        "getblockhash" => methods::get_block_hash(&context, request.params).await,
        "gettxout" => methods::get_tx_out(&context, request.params).await,
        
        // Network methods
        "getconnectioncount" => methods::get_connection_count(&context, request.params).await,
        "getpeerinfo" => methods::get_peer_info(&context, request.params).await,
        "getnetworkinfo" => methods::get_network_info(&context, request.params).await,
        
        // Transaction methods
        "sendrawtransaction" => methods::send_raw_transaction(&context, request.params).await,
        "getrawtransaction" => methods::get_raw_transaction(&context, request.params).await,
        "decoderawtransaction" => methods::decode_raw_transaction(&context, request.params).await,
        
        // Mining methods
        "getmininginfo" => methods::get_mining_info(&context, request.params).await,
        "submitblock" => methods::submit_block(&context, request.params).await,
        "getblocktemplate" => methods::get_block_template(&context, request.params).await,
        
        // Utility methods
        "validateaddress" => methods::validate_address(&context, request.params).await,
        
        // Unknown method
        _ => Err(RpcError::MethodNotFound(request.method)),
    };
    
    match result {
        Ok(result) => create_success_response(result, id),
        Err(error) => create_error_response(error, id),
    }
}

/// Create a success response
fn create_success_response(result: Value, id: Value) -> Response {
    let response = JsonRpcResponse {
        jsonrpc: "2.0".to_string(),
        result: Some(result),
        error: None,
        id,
    };
    
    (StatusCode::OK, Json(response)).into_response()
}

/// Create an error response
fn create_error_response(error: RpcError, id: Value) -> Response {
    let response = JsonRpcResponse {
        jsonrpc: "2.0".to_string(),
        result: None,
        error: Some(error.into()),
        id,
    };
    
    (StatusCode::OK, Json(response)).into_response()
}