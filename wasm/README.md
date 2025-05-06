# SmellyCoin WebAssembly Mining Module

This module provides WebAssembly bindings for SmellyCoin mining operations, enabling web-based mining through browsers and integration with mining pools.

## Features

- WebAssembly-based KAWPOW mining implementation
- Web Worker support for non-blocking mining operations
- Mining pool integration via WebSocket proxy
- Configurable mining parameters (threads, throttling)
- Real-time hashrate monitoring

## Building

1. Install wasm-pack:
```bash
cargo install wasm-pack
```

2. Build the WebAssembly module:
```bash
cd wasm
wasm-pack build --target web
```

3. Copy static files:
```bash
cp static/* pkg/
```

## Integration

1. Include the built files in your web project:
```html
<script type="module">
    import init, { SmellyCoinMiner } from './pkg/smellycoin_wasm.js';

    async function startMining() {
        await init();
        
        const config = {
            pool: 'stratum+tcp://pool.example.com:8008',
            address: 'your-wallet-address',
            threads: 4,
            throttle: 0.8
        };
        
        const miner = new SmellyCoinMiner(config);
        await miner.start();
        
        // Monitor hashrate
        setInterval(() => {
            const hashrate = miner.get_hashrate();
            console.log(`Current hashrate: ${hashrate} H/s`);
        }, 1000);
    }

    startMining().catch(console.error);
</script>
```

## Development

1. Run tests:
```bash
wasm-pack test --node
```

2. Start development server:
```bash
npm install -g http-server
cd pkg
http-server
```

## Mining Pool Setup

To connect to a mining pool, you'll need to set up a WebSocket proxy that converts WebSocket connections to TCP for the Stratum protocol. Example proxy implementation can be found in the `tools/ws-proxy` directory.

## Performance Considerations

- Use the throttle parameter to control CPU usage and power consumption
- Monitor system temperature when mining on mobile devices
- Adjust thread count based on device capabilities
- Consider implementing progressive throttling based on battery status

## Security Notes

- Never expose private keys or wallet credentials in the browser
- Use secure WebSocket connections (wss://) for pool communication
- Implement rate limiting and validation in the WebSocket proxy
- Monitor for suspicious mining behavior