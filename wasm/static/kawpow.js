// KAWPOW Algorithm Implementation for WebAssembly

class KAWPOWHash {
    constructor() {
        this.initialized = false;
        this.wasmInstance = null;
    }

    async initialize() {
        if (this.initialized) return;

        try {
            const response = await fetch('./kawpow_bg.wasm');
            const wasmBytes = await response.arrayBuffer();
            const wasmModule = await WebAssembly.instantiate(wasmBytes, {
                env: {
                    memory: new WebAssembly.Memory({ initial: 17 }),
                    abort: () => { throw new Error('Wasm aborted'); }
                }
            });
            this.wasmInstance = wasmModule.instance;
            this.initialized = true;
        } catch (error) {
            console.error('Failed to initialize KAWPOW:', error);
            throw error;
        }
    }

    hash(header, nonce, height) {
        if (!this.initialized) {
            throw new Error('KAWPOW not initialized');
        }

        // Ensure inputs are correct type
        if (!(header instanceof Uint8Array) || header.length !== 32) {
            throw new Error('Header must be a 32-byte Uint8Array');
        }

        // Call the WASM implementation
        const result = new Uint8Array(32);
        this.wasmInstance.exports.kawpow_hash(
            header.buffer,
            nonce,
            height,
            result.buffer
        );

        return result;
    }

    verifyHash(hash, target) {
        if (!(hash instanceof Uint8Array) || hash.length !== 32) {
            throw new Error('Hash must be a 32-byte Uint8Array');
        }
        if (!(target instanceof Uint8Array) || target.length !== 32) {
            throw new Error('Target must be a 32-byte Uint8Array');
        }

        // Compare hash with target (little-endian)
        for (let i = 31; i >= 0; i--) {
            if (hash[i] < target[i]) return true;
            if (hash[i] > target[i]) return false;
        }
        return true;
    }
}

// Create and export a singleton instance
const kawpow = new KAWPOWHash();
export default kawpow;