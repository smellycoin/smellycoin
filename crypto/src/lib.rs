//! Cryptographic primitives for SmellyCoin

use secp256k1::{PublicKey, SecretKey, Message, Secp256k1};
use sha2::{Sha256, Digest};
use thiserror::Error;
use std::fmt;

#[derive(Error, Debug)]
pub enum CryptoError {
    #[error("Invalid key format")]
    InvalidKey,
    #[error("Signing failed")]
    SigningError,
    #[error("Verification failed")]
    VerificationError,
}

/// Represents a SmellyCoin address
pub struct Address(Vec<u8>);

impl Address {
    /// Create a new address from a public key
    pub fn from_pubkey(pubkey: &PublicKey) -> Self {
        let pubkey_bytes = pubkey.serialize_uncompressed();
        let mut hasher = Sha256::new();
        hasher.update(&pubkey_bytes);
        let hash = hasher.finalize();
        Address(hash[..20].to_vec())
    }

    /// Convert address to base58 string
    pub fn to_base58(&self) -> String {
        bs58::encode(&self.0).into_string()
    }

    /// Create address from base58 string
    pub fn from_base58(s: &str) -> Result<Self, CryptoError> {
        match bs58::decode(s).into_vec() {
            Ok(bytes) if bytes.len() == 20 => Ok(Address(bytes)),
            _ => Err(CryptoError::InvalidKey)
        }
    }
}

impl fmt::Display for Address {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}" , self.to_base58())
    }
}

/// Signs a message using a private key
pub fn sign_message(msg: &[u8], secret_key: &SecretKey) -> Result<Vec<u8>, CryptoError> {
    let secp = Secp256k1::new();
    let msg = Message::from_slice(&hash_message(msg))
        .map_err(|_| CryptoError::SigningError)?;
    
    Ok(secp.sign_ecdsa(&msg, secret_key).serialize_compact().to_vec())
}

/// Verifies a signature
pub fn verify_signature(
    msg: &[u8],
    signature: &[u8],
    public_key: &PublicKey
) -> Result<bool, CryptoError> {
    let secp = Secp256k1::new();
    let msg = Message::from_slice(&hash_message(msg))
        .map_err(|_| CryptoError::VerificationError)?;
    
    match secp.verify_ecdsa(
        &msg,
        &secp256k1::ecdsa::Signature::from_compact(signature)
            .map_err(|_| CryptoError::VerificationError)?,
        public_key
    ) {
        Ok(_) => Ok(true),
        Err(_) => Ok(false)
    }
}

/// Hashes a message using SHA256
pub fn hash_message(msg: &[u8]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(msg);
    hasher.finalize().into()
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::thread_rng;

    #[test]
    fn test_address_generation() {
        let secp = Secp256k1::new();
        let (secret_key, public_key) = secp.generate_keypair(&mut thread_rng());
        let address = Address::from_pubkey(&public_key);
        let base58 = address.to_base58();
        let decoded = Address::from_base58(&base58).unwrap();
        assert_eq!(address.0, decoded.0);
    }

    #[test]
    fn test_signing_and_verification() {
        let secp = Secp256k1::new();
        let (secret_key, public_key) = secp.generate_keypair(&mut thread_rng());
        let message = b"Hello, SmellyCoin!";
        
        let signature = sign_message(message, &secret_key).unwrap();
        let is_valid = verify_signature(message, &signature, &public_key).unwrap();
        assert!(is_valid);
    }
}