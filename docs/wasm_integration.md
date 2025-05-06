# SmellyCoin WASM Integration Guide

## Overview

SmellyCoin now supports WebAssembly (WASM) integration, enabling web-based mining and wallet functionality. This document explains how to set up and use the WASM features for mining on browsers and mobile devices, as well as integrating web wallets.

## Features

- **Browser-based mining**: Mine SmellyCoin directly from web browsers without additional software
- **Mobile mining**: Optimized mining for mobile devices with battery and temperature considerations
- **Web wallet integration**: Full wallet functionality in web applications
- **Lightweight operation**: Uses block headers for efficient mining without downloading the full blockchain
- **Cross-platform compatibility**: Works on desktop and mobile browsers

## Setting Up WASM Mining

### For Website Owners

To integrate SmellyCoin mining into your website:

1. Include the SmellyCoin WASM library in your HTML:

```html
<script src="/js/smellycoin-miner.js"></script>
```

2. Initialize the miner with configuration options:

```javascript
const miner = new SmellyCoinMiner({
  pool: 'stratum+tcp://pool_address:port',
  address: 'your_wallet_address',
  threads: 4,  // Number of threads to use
  throttle: 0.5  // Throttle factor (0-1)
});
```

3. Add controls to start and stop mining:

```javascript
// Start mining
document.getElementById('start-mining').addEventListener('click', () => {
  miner.start();
});

// Stop mining
document.getElementById('stop-mining').addEventListener('click', () => {
  miner.stop();
});

// Display hashrate
setInterval(() => {
  const hashrate = miner.get_hashrate();
  document.getElementById('hashrate').textContent = `${hashrate.toFixed(2)} H/s`;
}, 1000);
```

### For Users

As a user, you can mine SmellyCoin by:

1. Visiting a website that integrates SmellyCoin mining
2. Providing your wallet address
3. Starting the mining process through the website interface

## Mobile Mining Optimization

The WASM mining implementation includes specific optimizations for mobile devices:

### Battery Efficiency

- **Adaptive throttling**: Automatically adjusts mining intensity based on battery level
- **Background throttling**: Reduces intensity when the browser tab is not active
- **Pause on low battery**: Automatically pauses mining when battery level is critical

### Temperature Management

- **Thermal monitoring**: Detects device temperature increases
- **Cool-down periods**: Implements periodic pauses to prevent overheating
- **Gradual intensity**: Slowly increases mining intensity to avoid thermal spikes

### Mobile-Specific Settings

```javascript
const mobileMiner = new SmellyCoinMiner({
  pool: 'stratum+tcp://pool_address:port',
  address: 'your_wallet_address',
  threads: 2,  // Fewer threads for mobile
  throttle: 0.7,  // Higher throttle (more pauses) for mobile
  batteryMonitoring: true,
  thermalProtection: true
});
```

## Web Wallet Integration

### Setting Up a Web Wallet

1. Include the SmellyCoin wallet library:

```html
<script src="/js/smellycoin-wallet.js"></script>
```

2. Initialize the wallet:

```javascript
const wallet = new SmellyCoinWallet();

// Connect to a node
wallet.connect('https://node_address:port');

// Generate a new address
const address = wallet.generate_address();
document.getElementById('wallet-address').textContent = address;
```

3. Implement wallet functionality:

```javascript
// Get balance
async function updateBalance() {
  try {
    const balance = await wallet.get_balance();
    document.getElementById('balance').textContent = balance;
  } catch (error) {
    console.error('Error getting balance:', error);
  }
}

// Send transaction
async function sendTransaction() {
  const recipient = document.getElementById('recipient').value;
  const amount = parseFloat(document.getElementById('amount').value);
  
  try {
    const txid = await wallet.send_transaction(recipient, amount);
    alert(`Transaction sent! TXID: ${txid}`);
  } catch (error) {
    alert(`Error: ${error}`);
  }
}

// View transaction history
async function showTransactionHistory() {
  try {
    const history = await wallet.get_transaction_history();
    const historyElement = document.getElementById('tx-history');
    historyElement.innerHTML = '';
    
    history.forEach(tx => {
      const txElement = document.createElement('div');
      txElement.textContent = tx;
      historyElement.appendChild(txElement);
    });
  } catch (error) {
    console.error('Error getting transaction history:', error);
  }
}
```

### Security Considerations

- **Private key encryption**: Keys are encrypted with a user-provided password
- **Local storage**: Private keys never leave the user's device
- **Secure connections**: All communication with nodes uses HTTPS
- **Session timeouts**: Automatic logout after periods of inactivity

## Technical Implementation

### Mining Architecture

The WASM mining implementation uses a multi-threaded approach:

1. **Main thread**: Handles UI updates and coordination
2. **Worker threads**: Perform the actual mining computations
3. **Stratum client**: Communicates with mining pools via WebSocket proxy

### Block Header Mining

The mining process uses only block headers, which provides several advantages:

1. **Reduced bandwidth**: Headers are ~80 bytes vs. potentially megabytes for full blocks
2. **Faster startup**: Miners can begin work almost immediately
3. **Lower memory usage**: Important for mobile devices

### WebAssembly Optimizations

The WASM code is optimized for performance:

- **SIMD instructions**: Uses SIMD when available for faster hashing
- **Memory management**: Minimizes allocations during mining
- **Compact code**: Optimized for small download size
- **Lazy loading**: Components are loaded only when needed

## Integration with Database Backend

The WASM implementation works seamlessly with the database backend:

1. **Efficient queries**: Web clients can request minimal data
2. **Header synchronization**: Wallets can sync using only headers
3. **Lightweight validation**: Transactions can be verified without the full blockchain

## Browser Compatibility

The WASM mining and wallet functionality is compatible with:

- **Chrome/Edge**: Version 79+
- **Firefox**: Version 72+
- **Safari**: Version 14.1+
- **Mobile Chrome**: Version 88+
- **Mobile Safari**: Version 14.5+

## Development and Customization

Developers can customize and extend the WASM functionality:

1. Build from source:

```bash
cd wasm
wasm-pack build --target web
```

2. Customize the worker implementation in `static/smellycoin_worker.js`

3. Implement additional features by extending the Rust code in `src/lib.rs`

## Conclusion

The WASM integration provides a powerful way to extend SmellyCoin's reach to web browsers and mobile devices. By combining efficient mining using block headers with the database backend, SmellyCoin offers a complete solution for web-based cryptocurrency operations that is both fast and accessible to a wide range of users and devices.