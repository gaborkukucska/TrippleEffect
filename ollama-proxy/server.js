import express from 'express';
import fetch from 'node-fetch'; // Use node-fetch v3 which supports streaming well
import process from 'process'; // Import process to access env variables

// --- START OF PROXY SCRIPT ---
console.log(`[PROXY_DEBUG] Script starting execution. Timestamp: ${new Date().toISOString()}`);

const app = express();

// --- Configuration from Environment Variables ---
const port = parseInt(process.env.OLLAMA_PROXY_PORT || '3000', 10);
const ollamaTarget = process.env.OLLAMA_PROXY_TARGET_URL || 'http://localhost:11434';
console.log(`[PROXY_DEBUG] Config - Port: ${port}, Target: ${ollamaTarget}`);
// --- End Configuration ---

app.use(express.json()); // Middleware to parse JSON bodies

// Proxy route for /api/chat (POST, streaming)
app.post('/api/chat', async (req, res) => {
  const targetUrl = `${ollamaTarget}/api/chat`;
  console.log(`[PROXY_INFO] Received POST request for /api/chat. Target: ${targetUrl}`);
  // console.log(`[PROXY_DEBUG] Request Body:`, JSON.stringify(req.body).substring(0, 200) + '...'); // Keep truncated

  try {
    const ollamaResponse = await fetch(targetUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/x-ndjson',
      },
      body: JSON.stringify(req.body),
    });

    if (!ollamaResponse.ok) {
      const errorBody = await ollamaResponse.text();
      console.error(`[PROXY_ERROR] Ollama server error (${ollamaResponse.status}) for /api/chat: ${errorBody}`);
      res.status(ollamaResponse.status).send(errorBody);
      return;
    }

    res.setHeader('Content-Type', 'application/x-ndjson');
    res.setHeader('Transfer-Encoding', 'chunked');
    console.log(`[PROXY_INFO] Piping stream response for /api/chat...`);
    ollamaResponse.body.pipe(res);

    ollamaResponse.body.on('end', () => {
      console.log(`[PROXY_INFO] Stream finished for POST request to ${targetUrl}`);
    });
    ollamaResponse.body.on('error', (err) => {
      console.error(`[PROXY_ERROR] Error piping stream from Ollama for /api/chat:`, err);
      if (!res.headersSent) {
        res.status(500).send('Proxy stream error');
      }
      res.end();
    });

  } catch (error) {
    console.error(`[PROXY_ERROR] Error fetching /api/chat from Ollama:`, error);
    if (!res.headersSent) {
        res.status(502).send(`Proxy error fetching /api/chat: ${error.message}`);
    } else {
        res.end();
    }
  }
});

// Proxy route for /api/tags (GET, non-streaming JSON)
app.get('/api/tags', async (req, res) => {
  // --- ADDED DEBUG LOG ---
  console.log(`[PROXY_DEBUG] Reached /api/tags handler. Timestamp: ${new Date().toISOString()}`);
  const targetUrl = `${ollamaTarget}/api/tags`;
  console.log(`[PROXY_INFO] Received GET request for /api/tags. Target: ${targetUrl}`);

  try {
    const ollamaResponse = await fetch(targetUrl, {
      method: 'GET',
      headers: {
        'Accept': 'application/json',
      },
    });

    res.status(ollamaResponse.status);
    ollamaResponse.headers.forEach((value, name) => {
      const lowerCaseName = name.toLowerCase();
      if (lowerCaseName !== 'transfer-encoding' && lowerCaseName !== 'content-encoding' && lowerCaseName !== 'connection') {
         res.setHeader(name, value);
      }
    });

    const contentType = ollamaResponse.headers.get('content-type');
    if (contentType) {
        res.setHeader('Content-Type', contentType);
    } else {
        res.setHeader('Content-Type', 'application/json');
    }

    console.log(`[PROXY_INFO] Piping JSON response for /api/tags... Status: ${ollamaResponse.status}`);
    ollamaResponse.body.pipe(res);

    ollamaResponse.body.on('error', (err) => {
      console.error(`[PROXY_ERROR] Error piping /api/tags response from Ollama:`, err);
      if (!res.headersSent) {
        res.status(500).send('Proxy stream error for /api/tags');
      }
      res.end();
    });
     ollamaResponse.body.on('end', () => {
      console.log(`[PROXY_INFO] Finished piping response for GET request to ${targetUrl}`);
    });

  } catch (error) {
    console.error(`[PROXY_ERROR] Error fetching /api/tags from Ollama:`, error);
    if (!res.headersSent) {
        res.status(502).send(`Proxy error fetching /api/tags: ${error.message}`);
    } else {
        res.end();
    }
  }
});


// Basic root route for testing if the proxy is running
app.get('/', (req, res) => {
  console.log(`[PROXY_INFO] Received GET request for /`);
  res.send('Ollama Proxy is running!');
});

// --- Catch-all for unhandled routes ---
app.use((req, res, next) => {
  console.error(`[PROXY_ERROR] Unhandled route: ${req.method} ${req.originalUrl}`);
  res.status(404).send(`Proxy Error: Cannot ${req.method} ${req.originalUrl}`);
});


app.listen(port, '0.0.0.0', () => {
  console.log(`[PROXY_INFO] Ollama proxy server listening on http://0.0.0.0:${port}`);
  console.log(`[PROXY_INFO] Forwarding requests to: ${ollamaTarget}`);
});

// --- Log uncaught exceptions ---
process.on('uncaughtException', (err) => {
  console.error('[PROXY_FATAL] Uncaught Exception:', err);
  process.exit(1); // Exit on fatal error
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('[PROXY_FATAL] Unhandled Rejection at:', promise, 'reason:', reason);
  // Optionally exit, depending on whether these are considered fatal
});

console.log(`[PROXY_DEBUG] Script finished initial setup. Waiting for server to listen...`);
// --- END OF PROXY SCRIPT ---
