// SmellyCoin WebSocket to Stratum Proxy

const WebSocket = require('ws');
const net = require('net');
const crypto = require('crypto');

// Configuration
const WS_PORT = process.env.WS_PORT || 8089;
const RATE_LIMIT = {
    windowMs: 60000, // 1 minute
    maxRequests: 100 // max requests per window
};

// Create WebSocket server
const wss = new WebSocket.Server({ port: WS_PORT });

// Track client connections and rate limiting
const clients = new Map();
const rateLimit = new Map();

console.log(`WebSocket proxy listening on port ${WS_PORT}`);

wss.on('connection', (ws, req) => {
    const clientId = crypto.randomBytes(16).toString('hex');
    let stratumSocket = null;

    // Rate limiting check
    const clientIp = req.socket.remoteAddress;
    if (isRateLimited(clientIp)) {
        ws.close(1008, 'Rate limit exceeded');
        return;
    }

    console.log(`Client connected: ${clientId}`);

    ws.on('message', async (message) => {
        try {
            const data = JSON.parse(message);

            // Handle initial connection message
            if (data.type === 'connect') {
                if (!data.pool || !data.pool.startsWith('stratum+tcp://')) {
                    throw new Error('Invalid pool URL');
                }

                const poolUrl = new URL(data.pool.replace('stratum+tcp://', 'tcp://'));
                stratumSocket = await connectToPool(poolUrl.hostname, poolUrl.port);

                // Handle data from pool
                stratumSocket.on('data', (poolData) => {
                    ws.send(JSON.stringify({
                        type: 'pool_data',
                        data: poolData.toString()
                    }));
                });

                clients.set(clientId, { ws, stratumSocket });
            }
            // Forward mining data to pool
            else if (data.type === 'mining_submit' && stratumSocket) {
                stratumSocket.write(JSON.stringify(data.data) + '\n');
            }
        } catch (error) {
            console.error(`Error handling message from ${clientId}:`, error);
            ws.send(JSON.stringify({
                type: 'error',
                message: error.message
            }));
        }
    });

    ws.on('close', () => {
        console.log(`Client disconnected: ${clientId}`);
        if (stratumSocket) {
            stratumSocket.destroy();
        }
        clients.delete(clientId);
    });

    ws.on('error', (error) => {
        console.error(`WebSocket error for ${clientId}:`, error);
        if (stratumSocket) {
            stratumSocket.destroy();
        }
        clients.delete(clientId);
    });
});

// Connect to mining pool
function connectToPool(host, port) {
    return new Promise((resolve, reject) => {
        const socket = new net.Socket();

        socket.on('connect', () => {
            console.log(`Connected to pool: ${host}:${port}`);
            resolve(socket);
        });

        socket.on('error', (error) => {
            console.error('Pool connection error:', error);
            reject(error);
        });

        socket.connect(port, host);
    });
}

// Rate limiting implementation
function isRateLimited(clientIp) {
    const now = Date.now();
    const clientRate = rateLimit.get(clientIp) || {
        count: 0,
        windowStart: now
    };

    // Reset window if expired
    if (now - clientRate.windowStart > RATE_LIMIT.windowMs) {
        clientRate.count = 0;
        clientRate.windowStart = now;
    }

    clientRate.count++;
    rateLimit.set(clientIp, clientRate);

    return clientRate.count > RATE_LIMIT.maxRequests;
}

// Cleanup old rate limit entries periodically
setInterval(() => {
    const now = Date.now();
    for (const [ip, rate] of rateLimit.entries()) {
        if (now - rate.windowStart > RATE_LIMIT.windowMs) {
            rateLimit.delete(ip);
        }
    }
}, RATE_LIMIT.windowMs);